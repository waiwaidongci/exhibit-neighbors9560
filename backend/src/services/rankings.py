import secrets,hashlib,json
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from redis import Redis
from ..config.settings import REDIS_URL
from ..models.ranking import Ranking
from ..utils.serialization import row,now
from .sources import source_or_404,list_records
from .ranking_algorithm import normalize_ratings,pair_votes_from,MAX_EXHIBITS
from .audit import log
queue=Redis.from_url(REDIS_URL)
def get_ranking(db,id):
    r=db.get(Ranking,id)
    if not r:raise HTTPException(404,'未找到排位研究')
    return r
def _selected(db,source_id,selected):
    cleaned=sorted({str(code).strip() for code in selected if str(code).strip()})
    if len(cleaned)<2:raise HTTPException(422,'至少选择2件展品才能排位')
    if len(cleaned)>MAX_EXHIBITS:raise HTTPException(422,'最多选择16件展品')
    return cleaned
def _seal(db,source_id,cleaned):
    """Freeze selection, per-visitor ratings and resulting votes at acceptance."""
    source=source_or_404(db,source_id)
    records=list_records(db,source_id)
    chosen=set(cleaned)
    ratings=[
        {'visitor':r['values']['visitor'],'exhibit':r['values']['exhibit'],'rating':float(r['values']['rating'])}
        for r in records if r['values']['exhibit'] in chosen
    ]
    # Records arrive in database id order, so the last duplicate already wins.
    normalized=normalize_ratings(ratings)
    present={item['exhibit'] for item in normalized}
    missing=[code for code in cleaned if code not in present]
    if missing:raise HTTPException(422,'选品中没有评分记录：'+'、'.join(missing))
    pair_votes=pair_votes_from(normalized)
    if not pair_votes:raise HTTPException(422,'所选展品之间没有可比较的评分')
    snapshot={
        'source':row(source),
        'selected':cleaned,
        'ratings':normalized,
        'sealed_at':now(),
        'seal_rule':'同一访客同一展品以最后录入的评分记录为准；并发补评不改变本次封存票数',
    }
    frozen_votes=[[winner,loser,count] for (winner,loser),count in sorted(pair_votes.items())]
    checksum=hashlib.sha256(
        json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()
    ).hexdigest()
    snapshot['checksum']=checksum
    return snapshot,frozen_votes
def create_ranking(db,user,data):
    cleaned=_selected(db,data.source_id,data.selected)
    request_key=data.request_key.strip() or 'auto-'+secrets.token_hex(12)
    existing=db.scalar(select(Ranking).where(Ranking.request_key==request_key))
    if existing:
        if existing.source_id!=data.source_id or sorted(existing.selected)!=cleaned:
            raise HTTPException(409,'该排位请求已用于另一组选品')
        if existing.state=='failed':
            return retry_ranking(db,user,existing.id)
        return {**ranking_view(existing),'deduplicated':True,'created':False}
    snapshot,frozen_votes=_seal(db,data.source_id,cleaned)
    r=Ranking(source_id=data.source_id,request_key=request_key,name=data.name,
              state='queued',progress=10,stage='已受理，等待 worker',selected=cleaned,
              snapshot=snapshot,frozen_votes=frozen_votes,created_by=user.email,created_at=now())
    db.add(r)
    try:
        db.flush();log(db,user,'ranking.queued',r.id,data.name);db.commit()
    except IntegrityError:
        # A concurrent request won the same request_key; reuse its report.
        db.rollback()
        existing=db.scalar(select(Ranking).where(Ranking.request_key==request_key))
        if existing:
            if existing.source_id!=data.source_id or sorted(existing.selected)!=cleaned:
                raise HTTPException(409,'该排位请求已用于另一组选品')
            return {**ranking_view(existing),'deduplicated':True,'created':False}
        raise HTTPException(409,'该排位请求正在处理，请稍后刷新')
    try:queue.rpush('ranking:queue',r.id)
    except Exception:
        r.state='failed';r.error='队列暂不可用，请重新发起排位';db.commit()
        raise HTTPException(503,r.error)
    return {**ranking_view(r),'deduplicated':False,'created':True}
def retry_ranking(db,user,id):
    r=get_ranking(db,id)
    if r.state!='failed':
        if r.state=='completed':return {**ranking_view(r),'deduplicated':True,'created':False}
        raise HTTPException(409,'该排位研究仍在处理中')
    # Same sealed selection, snapshot and votes; no new report row is created.
    r.state='queued';r.progress=10;r.stage='已用同一封存重新排队';r.error=''
    log(db,user,'ranking.retried',r.id,r.name);db.commit()
    try:queue.rpush('ranking:queue',r.id)
    except Exception:
        r.state='failed';r.error='队列暂不可用，请稍后重试';db.commit()
        raise HTTPException(503,r.error)
    return {**ranking_view(r),'deduplicated':True,'created':False}
def ranking_view(r):
    data=row(r)
    data['selected']=r.selected
    data['example_pair']=None
    if r.result:
        data['result']=r.result
        data['example_pair']=r.result.get('example_pair')
    else:
        data['result']=None
    if data['snapshot'] and 'records' in data['snapshot']:
        data['snapshot']={k:v for k,v in data['snapshot'].items() if k!='records'}
    return data
def list_rankings(db):
    # The ledger only needs identity, state and time; keep heavy frozen votes
    # and results out of the list payload.
    light=[]
    for r in db.scalars(select(Ranking).order_by(Ranking.id.desc())):
        data=ranking_view(r)
        data['result']=None
        data['frozen_votes']=None
        data['snapshot']=None
        data['example_pair']=None
        light.append(data)
    return light

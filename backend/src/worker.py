import hashlib,json,time,logging
from redis import Redis
from .config.database import Session
from .config.settings import REDIS_URL,ARTIFACT_DIR
from .models.run import Run
from .models.ranking import Ranking
from .models import source,record,user,note,audit
from .services.algorithm import calculate
from .services.ranking_algorithm import rank
from .services.audit import log
from .services.export import csv_response,ranking_csv
queue=Redis.from_url(REDIS_URL)
ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
def execute(id):
    with Session() as db:
        r=db.get(Run,id)
        if not r or r.state!='queued':return
        r.state='running';db.commit()
        try:
            result=calculate(r.snapshot['records'],r.parameters)
            json.dumps(result,allow_nan=False)
            r.result=result;r.state='completed'
            response=csv_response(result,'run.csv')
            (ARTIFACT_DIR/f'run-{r.id}.csv').write_bytes(response.body)
            log(db,'worker','run.completed',r.id)
        except Exception as e:
            logging.exception('analysis failed');r.state='failed';r.error=str(e)[:2000]
            log(db,'worker','run.failed',r.id,r.error)
        db.commit()
def execute_ranking(id):
    with Session() as db:
        r=db.get(Ranking,id)
        if not r or r.state!='queued':return
        r.state='running';r.progress=30;r.stage='正在还原封存票数';db.commit()
        def progress(percent,stage):
            # Keep the page showing progress only; no partial report is exposed.
            r.progress=percent;r.stage=stage;db.commit()
        try:
            pair_votes={(w,l):c for w,l,c in r.frozen_votes}
            snapshot=r.snapshot or {}
            ratings=snapshot.get('ratings',[])
            result=rank(r.selected,pair_votes,
                        visitors=len({x['visitor'] for x in ratings}),
                        ratings_used=len(ratings),
                        checksum=snapshot.get('checksum') or hashlib.sha256(
                            json.dumps(ratings,sort_keys=True,ensure_ascii=False).encode()
                        ).hexdigest(),
                        progress=progress)
            json.dumps(result,allow_nan=False)
            r.result=result;r.state='completed';r.progress=100;r.stage='已完成'
            response=ranking_csv(result,'ranking.csv')
            (ARTIFACT_DIR/f'ranking-{r.id}.csv').write_bytes(response.body)
            log(db,'worker','ranking.completed',r.id)
        except Exception as e:
            logging.exception('ranking failed');r.state='failed';r.error=str(e)[:2000]
            r.stage='失败';log(db,'worker','ranking.failed',r.id,r.error)
        db.commit()
def main():
    while True:
        (ARTIFACT_DIR/'heartbeat').write_text(str(time.time()))
        value=queue.blpop(['ranking:queue','analysis:queue'],timeout=2)
        if not value:continue
        channel,payload=value
        id=int(payload)
        if channel==b'ranking:queue':execute_ranking(id)
        else:execute(id)
if __name__=='__main__':main()

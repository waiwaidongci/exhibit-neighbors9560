from collections import defaultdict
import hashlib,json,math
import numpy as np
from .domain_validation import validate_parameters
def fit_ratings(data):
    users=defaultdict(dict)
    for r in data:
        if r['exhibit'] in users[r['visitor']]:raise ValueError('同一访客对同一展品重复评分')
        users[r['visitor']][r['exhibit']]=float(r['rating'])
    return dict(users)
def predict(users,visitor,exhibit,k,min_common):
    allratings=[v for items in users.values() for v in items.values()];globalmean=float(np.mean(allratings)) if allratings else 3.
    itemvals=[items[exhibit] for items in users.values() if exhibit in items]
    fallback=float(np.mean(itemvals)) if itemvals else globalmean
    if visitor not in users or len(users[visitor])<2:return fallback,[],'展品均值' if itemvals else '全局均值'
    target=users[visitor];targetmean=float(np.mean(list(target.values())));neighbors=[]
    for uid,items in users.items():
        if uid==visitor or exhibit not in items:continue
        common=sorted(set(target)&set(items))
        if len(common)<min_common:continue
        othermean=float(np.mean(list(items.values())));a=np.array([target[i]-targetmean for i in common]);b=np.array([items[i]-othermean for i in common]);den=float(np.linalg.norm(a)*np.linalg.norm(b))
        sim=float(a@b/den) if den>0 else 0.
        if sim>0:neighbors.append((uid,sim,items[exhibit]-othermean))
    neighbors.sort(key=lambda x:(-x[1],x[0]));neighbors=neighbors[:k]
    if not neighbors:return fallback,[],'展品均值' if itemvals else '全局均值'
    pred=targetmean+sum(s*d for _,s,d in neighbors)/sum(s for _,s,_ in neighbors)
    return min(5.,max(1.,pred)),[{'visitor':u,'similarity':s,'centered_rating':d} for u,s,d in neighbors],'相似邻居'
def calculate(records,parameters):
    p=validate_parameters(parameters);data=[r['values'] for r in records];users=fit_ratings(data)
    if len(users)<3:raise ValueError('推荐研究至少3位匿名访客')
    train=[];holdout=[]
    for uid,items in sorted(users.items()):
        ordered=sorted(items.items())
        for exhibit,rating in (ordered[:-1] if len(ordered)>=3 else ordered):train.append({'visitor':uid,'exhibit':exhibit,'rating':rating})
        if len(ordered)>=3:
            exhibit,rating=ordered[-1];holdout.append({'visitor':uid,'exhibit':exhibit,'rating':rating})
    if not holdout:raise ValueError('至少一位访客有3条评分才可留一验证')
    fitted=fit_ratings(train);evaluations=[]
    for r in holdout:
        pred,neighbors,method=predict(fitted,r['visitor'],r['exhibit'],p['neighbors'],p['min_common'])
        evaluations.append({**r,'prediction':pred,'method':method,'neighbor_count':len(neighbors)})
    rmse=math.sqrt(sum((x['prediction']-x['rating'])**2 for x in evaluations)/len(evaluations))
    # Production suggestions use the complete observed matrix after evaluation; never evaluate on these fitted scores.
    items=sorted({r['exhibit'] for r in data});suggestions=[]
    for uid,ratings in sorted(users.items()):
        candidates=[]
        for exhibit in items:
            if exhibit in ratings:continue
            pred,neighbors,method=predict(users,uid,exhibit,p['neighbors'],p['min_common'])
            candidates.append({'visitor':uid,'exhibit':exhibit,'prediction':pred,'method':method,'neighbor_count':len(neighbors),'neighbors':neighbors})
        suggestions.extend(sorted(candidates,key=lambda x:(-x['prediction'],x['exhibit']))[:3])
    rows=[{k:v for k,v in x.items() if k!='neighbors'} for x in suggestions]
    return {'summary':[{'label':'留一验证 RMSE','value':rmse},{'label':'匿名访客','value':len(users)},{'label':'留出评分','value':len(holdout)},{'label':'策展建议','value':len(rows)}], 'evaluation':evaluations,'training_ratings':train,'holdout_ratings':holdout,'recommendations':suggestions,'training_checksum':hashlib.sha256(json.dumps(train,sort_keys=True).encode()).hexdigest(),'rows':rows,'columns':['visitor','exhibit','prediction','method','neighbor_count'],'chart':[{'label':x['visitor']+' · '+x['exhibit'],'value':x['prediction']} for x in rows[:10]],'explanation':'验证：每位至少3评分访客按展品代码固定留最后一件，其评分完全排除训练。建议：验证完成后使用全量已知评分，只推荐未评分展品；正相似度加权，缺邻居回退展品均值/全局均值。','checksum':hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(),'seed':42}

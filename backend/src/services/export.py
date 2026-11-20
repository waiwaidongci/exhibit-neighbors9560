import csv,io
from fastapi import HTTPException
from fastapi.responses import Response
from ..models.note import Note
from .runs import get_run
def csv_response(result,filename):
    buf=io.StringIO();fields=result['columns'];w=csv.DictWriter(buf,fieldnames=fields);w.writeheader()
    for r in result['rows']:w.writerow({k:r.get(k,'') for k in fields})
    return Response('﻿'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})
def run_csv(db,id):
    r=get_run(db,id)
    if r.state!='completed':raise HTTPException(409,'结果尚未完成')
    return csv_response(r.result,'run-'+str(id)+'.csv')
def note_csv(db,id):
    n=db.get(Note,id)
    if not n:raise HTTPException(404,'未找到说明')
    if n.state!='approved':raise HTTPException(403,'仅批准后的业务说明可导出')
    return csv_response(n.frozen_result,'approved-'+str(id)+'.csv')
def ranking_csv(result,filename):
    buf=io.StringIO();w=csv.writer(buf)
    w.writerow(['排位','展品代号','违背票数','总票数','未定对数'])
    for rank_index,code in enumerate(result['order'],1):
        w.writerow([rank_index,code,result['violations'],result['total_votes'],result['ambiguous_count']])
    buf.write('\n')
    w.writerow(['未定对','展品甲','展品乙'])
    for a,b in result['ambiguous_pairs']:w.writerow(['未定',a,b])
    buf.write('\n')
    for example in result['examples']:
        w.writerow(['同样最优次序','违背票数',example['violations']])
        w.writerow(['次序']+example['order'])
    return Response('﻿'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})

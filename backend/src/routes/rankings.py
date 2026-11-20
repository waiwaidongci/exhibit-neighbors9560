from fastapi import APIRouter,Depends,HTTPException
from ..config.database import db_session
from ..schemas.ranking import RankingInput
from ..services.auth import current_user,writer
from ..services.export import ranking_csv
from ..services.rankings import get_ranking,ranking_view,retry_ranking
from ..controllers.rankings import RankingsController as C
router=APIRouter()
@router.get('/rankings')
def rankings(db=Depends(db_session),u=Depends(current_user)):return C.list(db)
@router.post('/rankings')
def create(data:RankingInput,db=Depends(db_session),u=Depends(writer)):return C.create(db,u,data)
@router.post('/rankings/{id}/retry')
def retry(id:int,db=Depends(db_session),u=Depends(writer)):return retry_ranking(db,u,id)
@router.get('/rankings/{id}')
def detail(id:int,db=Depends(db_session),u=Depends(current_user)):return ranking_view(get_ranking(db,id))
@router.get('/rankings/{id}/export')
def export(id:int,db=Depends(db_session),u=Depends(current_user)):
    r=get_ranking(db,id)
    if r.state!='completed':raise HTTPException(409,'结果尚未完成')
    return ranking_csv(r.result,'ranking-'+str(id)+'.csv')

from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey,UniqueConstraint
from ..config.database import Base
from ..config.settings import CONFIG
class Ranking(Base):
    __tablename__=CONFIG['rank_table']
    __table_args__=(UniqueConstraint('request_key',name='uq_ranking_request_key'),)
    id=Column(Integer,primary_key=True)
    source_id=Column(Integer,ForeignKey(CONFIG['source_table']+'.id'),nullable=False)
    request_key=Column(String(120),nullable=False)
    name=Column(String(160),nullable=False)
    state=Column(String(20),default='queued')
    progress=Column(Integer,default=0)
    stage=Column(String(80),default='已受理，等待 worker')
    selected=Column(JSON,nullable=False)
    snapshot=Column(JSON,nullable=False)
    frozen_votes=Column(JSON,nullable=False)
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    created_by=Column(String(120),default='')
    created_at=Column(String(40),nullable=False)

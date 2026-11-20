from pydantic import BaseModel,Field
class RankingInput(BaseModel):
    source_id:int
    name:str=Field(min_length=2,max_length=160)
    selected:list[str]=Field(min_length=2,max_length=16)
    request_key:str=Field(default='',max_length=120)

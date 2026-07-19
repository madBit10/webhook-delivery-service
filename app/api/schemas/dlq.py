from pydantic import BaseModel

class ReplayResult(BaseModel):
    replayed: list[int]
    count: int
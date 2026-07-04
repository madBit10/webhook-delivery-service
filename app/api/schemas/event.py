from pydantic import BaseModel, ConfigDict
from typing import Any
from datetime import datetime

class EventCreate(BaseModel):
    endpoint_id: int
    event_type: str
    payload: dict[str, Any]

class EventRead(BaseModel):
    id: int
    endpoint_id: int
    event_type: str
    payload: dict[str, Any]
    status: str
    created_at: datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.api.schemas.event import EventCreate, EventRead
from app.services.event import emit_event

router = APIRouter()

@router.post("/events", response_model=EventRead, status_code=201)
def emit_event_route(data: EventCreate, db:Session = Depends(get_db)):
    event = emit_event(db, data)
    if event is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return event
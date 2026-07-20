from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.api.schemas.event import EventCreate, EventRead
from app.services.event import emit_event, get_event, get_events
from app.db.redis_client import enqueue_event

router = APIRouter()

@router.post("/events", response_model=EventRead, status_code=201)
def emit_event_route(data: EventCreate, db:Session = Depends(get_db)):
    event = emit_event(db, data) # store as pending
    if event is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    enqueue_event(event.id) # store the event in the queue, async
    return event # return the final event 

# get the events from the event table

@router.get("/events", response_model=list[EventRead])
def list_events_route(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return get_events(db, skip, limit)

# get a specific event using the event id

@router.get("/events/{event_id}", response_model=EventRead)
def get_event_route(event_id: int, db: Session = Depends(get_db)):
    event = get_event(db, event_id)

    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
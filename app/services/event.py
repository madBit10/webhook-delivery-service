from sqlalchemy.orm import Session
from typing import Optional

from app.api.schemas.event import EventCreate
from app.db.repository import create_event, get_endpoint as get_endpoint_repo
from app.db.model import Event

def emit_event(db: Session, data: EventCreate) -> Optional[Event]:
    endpoint = get_endpoint_repo(db, data.endpoint_id) # does the endpoint exist?

    if endpoint is None:
        return None # signal "not found"

    return create_event(db, data.endpoint_id, data.event_type, data.payload)
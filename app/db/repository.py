from app.db.model import Endpoint
from sqlalchemy.orm import Session

def create_endpoint(db: Session, url: str, event_types: str, secret: str) -> Endpoint:
    endpoint = Endpoint(url=url, event_types=event_types, secret=secret)
    db.add(endpoint) # stage in the session
    db.commit() # write to DB, make permanent
    db.refresh(endpoint) # reload from DB so we get the generated id

    return endpoint



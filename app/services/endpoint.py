import secrets
from sqlalchemy.orm import Session

from app.api.schemas.endpoint import EndpointCreate
from app.db.repository import create_endpoint
from app.db.model import Endpoint

def register_endpoint(db: Session, data: EndpointCreate) -> Endpoint:
    secret = secrets.token_hex(32)
    endpoint = create_endpoint(
        db=db,
        url=data.url,
        event_types=data.event_types,
        secret=secret
    )

    return endpoint


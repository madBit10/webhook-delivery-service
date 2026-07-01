import secrets
from sqlalchemy.orm import Session
from typing import Optional

from app.api.schemas.endpoint import EndpointCreate
from app.db.repository import create_endpoint, get_endpoints as get_endpoints_repo, get_endpoint as get_endpoint_repo
from app.db.model import Endpoint

def register_endpoint(db: Session, data: EndpointCreate) -> Endpoint:
    secret = secrets.token_hex(32)
    endpoint = create_endpoint(
        db=db,
        url=str(data.url),
        event_types=data.event_types,
        secret=secret
    )

    return endpoint

# listing the list_enpoints in the services

def list_endpoints(db: Session, skip: int = 0, limit: int = 100) -> list[Endpoint]:

    return get_endpoints_repo(db, skip, limit)

# listing the get_endpoint in the services
def get_endpoint(db: Session, endpoint_id: int) -> Optional[Endpoint]:

    return get_endpoint_repo(db, endpoint_id)



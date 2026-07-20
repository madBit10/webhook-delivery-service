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

# listing the enpoints in the services

# Pass-through to the repo's paginated endpoint fetch. Kept as a service layer for consistency
# (Router → Service → Repository) and as a hook for future logic (filtering, auth).

def list_endpoints(db: Session, skip: int = 0, limit: int = 100) -> list[Endpoint]:

    return get_endpoints_repo(db, skip, limit)

# listing the get_endpoint in the services

# Pass-through to the repo's single-endpoint fetch. Returns None when missing — the route
# raises the 404, so error-shaping stays in the API layer, not here.
def get_endpoint(db: Session, endpoint_id: int) -> Optional[Endpoint]:

    return get_endpoint_repo(db, endpoint_id)



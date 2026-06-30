from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.api.schemas.endpoint import EndpointCreate, EndpointRead
from app.services.endpoint import register_endpoint

router = APIRouter()

@router.post("/endpoints", response_model=EndpointRead, status_code=201)
def create_endpoint_route(data: EndpointCreate, db: Session = Depends(get_db)):
    endpoint = register_endpoint(db,data)
    return endpoint
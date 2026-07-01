from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.api.schemas.endpoint import EndpointCreate, EndpointRead
from app.services.endpoint import register_endpoint, list_endpoints, get_endpoint

router = APIRouter()

@router.post("/endpoints", response_model=EndpointRead, status_code=201)
def create_endpoint_route(data: EndpointCreate, db: Session = Depends(get_db)):
    endpoint = register_endpoint(db,data)
    return endpoint

@router.get("/endpoints", response_model=list[EndpointRead])
def list_endpoints_route(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return list_endpoints(db, skip, limit)

@router.get("/endpoints/{endpoint_id}", response_model=EndpointRead)
def get_enpoint_route(endpoint_id: int, db: Session = Depends(get_db)):
    endpoint = get_endpoint(db, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return endpoint
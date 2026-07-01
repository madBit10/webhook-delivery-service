from app.db.model import Endpoint
from sqlalchemy.orm import Session
from typing import Optional


# create_endpoint inserts rows in the endpoint table
def create_endpoint(db: Session, url: str, event_types: str, secret: str) -> Endpoint:
    endpoint = Endpoint(url=url, event_types=event_types, secret=secret)
    db.add(endpoint) # stage in the session
    db.commit() # write to DB, make permanent
    db.refresh(endpoint) # reload from DB so we get the generated id

    return endpoint

# get all the endpoints in the form of the list from the endpoint table
def get_endpoints(db:Session, skip: int = 0, limit: int = 100) -> list[Endpoint]:

    endpoints = db.query(Endpoint).offset(skip).limit(limit).all() # querying all the endpoints from the endpoint table with pagination 

    return endpoints

# get a single object from the table by id i.e is getting the single endpoint by the id ex. id = 1

def get_endpoint(db:Session, endpoint_id: int) -> Optional[Endpoint]:

    single_endpoint = db.query(Endpoint).filter(Endpoint.id == endpoint_id)

    return single_endpoint.first()



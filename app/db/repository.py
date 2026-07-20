from app.db.model import Endpoint, Event, DeliveryAttempt
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional


# create_endpoint inserts rows in the endpoint table
def create_endpoint(db: Session, url: str, event_types: str, secret: str) -> Endpoint:
    endpoint = Endpoint(url=url, event_types=event_types, secret=secret)
    try:
        db.add(endpoint) # stage in the session
        db.commit() # write to DB, make permanent
        db.refresh(endpoint) # reload from DB so we get the generated id
    except SQLAlchemyError:
        db.rollback() # rollback if the error is encountered
        raise
    
    return endpoint

# get all the endpoints in the form of the list from the endpoint table
def get_endpoints(db:Session, skip: int = 0, limit: int = 100) -> list[Endpoint]:

    endpoints = db.query(Endpoint).offset(skip).limit(limit).all() # querying all the endpoints from the endpoint table with pagination 

    return endpoints

# get a single object from the table by id i.e is getting the single endpoint by the id ex. id = 1

def get_endpoint(db:Session, endpoint_id: int) -> Optional[Endpoint]:

    single_endpoint = db.query(Endpoint).filter(Endpoint.id == endpoint_id)

    return single_endpoint.first()

# inserting a new event row and return it - same add -> commit -> refresh

def create_event(db: Session, endpoint_id: int, event_type: str, payload: dict) -> Event: 
    event = Event(
        endpoint_id=endpoint_id, 
        event_type=event_type,
        payload=payload
        )
    try:
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    except SQLAlchemyError: 
        db.rollback()
        raise

# new function create_delivery_attempt - same as above -> add -> commit -> refresh

def create_delivery_attempt(db: Session, event_id: int, success: bool, response_status_code: Optional[int], response_body: Optional[str], attempt_number: int, duration_ms: Optional[int]) -> DeliveryAttempt:
    delivery_attempt = DeliveryAttempt(
        event_id = event_id,
        success = success,
        response_status_code = response_status_code,
        response_body = response_body,
        attempt_number = attempt_number,
        duration_ms = duration_ms

    )

    try:
        db.add(delivery_attempt)
        db.commit()
        db.refresh(delivery_attempt)
        return delivery_attempt
    except SQLAlchemyError:
        db.rollback()
        raise

# update the event status after the delivery attempt is done

def update_event_status(db: Session, event_id: int, new_status: str) -> Optional[Event]:

    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        return None
    event.status = new_status # mutate the loaded object

    try:
        db.commit()
        db.refresh(event)
        return event
    except SQLAlchemyError:
        db.rollback()
        raise


# function to get the event from the event id for the redis worker

# get the event from the event table using id of a particular event
def get_event(db:Session, event_id: int)->Optional[Event]:
    return db.query(Event).filter(Event.id == event_id).first()

# get all the events in the form of the list from the endpoint table
def get_events(db:Session, skip: int = 0, limit: int = 100) -> list[Event]:

    events = db.query(Event).offset(skip).limit(limit).all() # querying all the events from the event table with pagination 

    return events

# A repo helper to count prior attempts (this is how we derive the attempt_number)

def count_delivery_attempts(db: Session, event_id: int)-> int:
    # attempt_number = prior delivery attempts + 1

    return db.query(DeliveryAttempt).filter(DeliveryAttempt.event_id == event_id).count()

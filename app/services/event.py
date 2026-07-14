from sqlalchemy.orm import Session
from typing import Optional
import httpx
import time


from app.api.schemas.event import EventCreate
from app.db.repository import create_event, get_endpoint as get_endpoint_repo, create_delivery_attempt, update_event_status, count_delivery_attempts
from app.db.model import Event

def emit_event(db: Session, data: EventCreate) -> Optional[Event]:
    endpoint = get_endpoint_repo(db, data.endpoint_id) # does the endpoint exist?

    if endpoint is None:
        return None # signal "not found"

    return create_event(db, data.endpoint_id, data.event_type, data.payload)

# deliver event function - deliver_event is the function that actually tries to hand the event's data to the subscriber, records what happened, and marks the event done or failed.

def deliver_event(db: Session, event: Event) -> bool:

    # testing line
    # raise RuntimeError("boom")

    # get the endpoint (for its URL)

    endpoint = get_endpoint_repo(db, event.endpoint_id)

    # url from the endpoint
    url = endpoint.url

    # payload of the event
    payload = event.payload

    # attempt_number adding to the deliver event function to track the number of attempts made
    attempt_number = count_delivery_attempts(db, event.id) + 1

    # start time using time.perf_counter

    start = time.perf_counter()

    # POST with httpx
    try:
        response = httpx.post(url, json=payload, timeout=5.0)

        # got some response was it a 2xx?
        success = 200 <= response.status_code < 300
        status_code = response.status_code
        body = response.text

# if there is an error where the server request timedout or the connection was refused or there is bad DNS raise an httpx exception
    except httpx.RequestError as e: 
        # never got a response (timeout, connection refused, bad DNS)

        success = False
        status_code = None
        body = str(e)
    
    # duration of the HTTP call, how long did it take

    duration_ms = int((time.perf_counter() - start) * 1000) # seconds -> ms -> int
    #0 truncate the body so you don't store a giant page

    body = body[:1000]

    # log the attempt (whatever happend, record it )
    create_delivery_attempt(db, event.id, success, status_code, body, attempt_number, duration_ms)

    # # flip the event status based on success

    # new_status = "delivered" if success else "failed" # delivered or failed

    # updated_event = update_event_status(db, event.id, new_status)

    # return updated_event

    # now the deliver_event both delivers and sets the terminal, but the retry decision belongs to the worker

    # deliver event just attempts the POST, logs the attempt, returns a bool(sucess). Now it does not touch the event status

    return success



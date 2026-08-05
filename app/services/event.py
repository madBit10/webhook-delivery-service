from sqlalchemy.orm import Session
from typing import Optional
import httpx
import time
import json

from enum import Enum


from app.api.schemas.event import EventCreate
from app.db.repository import create_event, get_endpoint as get_endpoint_repo, create_delivery_attempt, count_delivery_attempts, get_events as get_events_repo, get_event as get_event_repo
from app.db.model import Event
from app.core.security import sign_payload

# define an enum with 3 states

class DeliveryOutcome(str, Enum):
    DELIVERED = "delivered"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"

# Pass-through to the repo's paginated event fetch. Kept as a service layer for consistency
# (Router → Service → Repository) and as a hook for future logic (filtering, auth).
def get_events(db: Session, skip: int = 0, limit: int = 100) -> list[Event]:

    return get_events_repo(db, skip, limit) # lists the events 

# Pass-through to the repo's single-event fetch. Returns None when missing — the route
# raises the 404, so error-shaping stays in the API layer, not here.
def get_event(db: Session, event_id: int) -> Optional[Event]:

    return get_event_repo(db, event_id)


# Ensure the endpoint exists, then persist the event as `pending` (delivery is async, done by the worker)
def emit_event(db: Session, data: EventCreate) -> Optional[Event]:
    endpoint = get_endpoint_repo(db, data.endpoint_id) # does the endpoint exist?

    if endpoint is None:
        return None # signal "not found"

    return create_event(db, data.endpoint_id, data.event_type, data.payload)

def classify_response(status_code: Optional[int]) -> DeliveryOutcome:

    # first check for None
    if status_code is None: # the events that had a timeout, connection refused or a DNS failure are the termed as retryable
        return DeliveryOutcome.RETRYABLE
    elif 200 <= status_code < 300:
        return DeliveryOutcome.DELIVERED
    elif status_code == 408 or status_code == 429:
        return DeliveryOutcome.RETRYABLE
    elif 400 <= status_code < 500:
        # terminal for now. 404 is arguable: it can be transient during a deploy,
        # but our retry window (~10s) is shorter than a deploy, so retrying gains nothing.
        # Revisit if the window grows to hours/days.
        return DeliveryOutcome.TERMINAL
    elif 500 <= status_code < 600:
        return DeliveryOutcome.RETRYABLE
    else:  # unknown / unexpected code 
        return DeliveryOutcome.TERMINAL
        

# deliver event function - deliver_event is the function that actually tries to hand the event's data to the subscriber, records what happened, and marks the event done or failed.

def deliver_event(db: Session, event: Event) -> DeliveryOutcome:

    # testing line
    # raise RuntimeError("boom")

    # get the endpoint (for its URL)

    endpoint = get_endpoint_repo(db, event.endpoint_id)

    # url from the endpoint
    url = endpoint.url

    # payload of the event
    payload = event.payload

    # serializing the payload 
    request_body = json.dumps(payload).encode()

    # attempt_number adding to the deliver event function to track the number of attempts made
    attempt_number = count_delivery_attempts(db, event.id) + 1

    # unix seconds using the time.time()

    timestamp = int(time.time())

    # hmac signature

    signature = sign_payload(endpoint.secret, timestamp, request_body)   

    # start time using time.perf_counter()
    
    start = time.perf_counter()

    # POST with httpx
    try:
        headers = {
            "Content-Type": "application/json",
            "X-Idempotency-Key": str(event.id),
            "X-Webhook-Timestamp": str(timestamp),
            "X-Webhook-Signature": signature
            } # header to recognize the duplicate events
        response = httpx.post(url, content=request_body, headers=headers, timeout=5.0)

        # got some response was it a 2xx?
        status_code = response.status_code
        body = response.text

# if there is an error where the server request timedout or the connection was refused or there is bad DNS raise an httpx exception
    except httpx.RequestError as e: 
        # never got a response (timeout, connection refused, bad DNS)
        status_code = None
        body = str(e)

    # setting the status code here from the classify response function
    outcome = classify_response(status_code)
    success = outcome is DeliveryOutcome.DELIVERED # the success states that the response would be 2xx
    
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

    # deliver event just attempts the POST, logs the attempt, returns outcome derived from the classify response function here. Now it does not touch the event status

    return outcome


from app.db.database import SessionLocal
from app.db.redis_client import client, QUEUE_KEY, enqueue_event
from app.db.repository import get_event, update_event_status, count_delivery_attempts
from app.services.event import deliver_event

MAX_ATTEMPTS = 5 # maximum retries const 

def run_worker() -> None:
    print("Worker started, waiting for events...")
    while True:
        # block until an id shows up; returns(queue_name, value)
        _, event_id = client.brpop(QUEUE_KEY, timeout=0)
        event_id = int(event_id) # comes backs as string so to int

        db = SessionLocal() # our own session (no Depends here)

        try:
            event = get_event(db, event_id)
            if event is None:
                print(f"Event {event_id} not found, skipping")
                continue
            # deliver_event(db, event) # reuse the Delivery here
            # print(f"Delivered event {event_id}, status={event.status}")

            # status changed handled by the worker was in the deliver event function previously

            success = deliver_event(db, event) # sucess flag from the deliver_event function
            # if the event succeeds then change the status to success
            if success:
                update_event_status(db, event.id, "delivered")
                print(f"Delivered event {event_id}")
            else:
                attempts = count_delivery_attempts(db, event.id) # store the delivery attempts in the attempts variable
                if attempts < MAX_ATTEMPTS:
                    enqueue_event(event.id) # retry - status is still pending for these events until the maximum attempts are made
                    print(f"Event {event_id} failed (attempt {attempts}/{MAX_ATTEMPTS}), re-queued")
                else:
                    update_event_status(db, event.id, "failed") # maximum number of the attempts reached the event status saved as failed
                    print(f"Event {event_id} failed permanently after {attempts} attempts")
        except Exception as e:
            print(f"Error processing event {event_id}: {e}")
        finally: 
            db.close() # always release the connection


if __name__ == "__main__":
    run_worker()
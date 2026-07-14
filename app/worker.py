from app.db.database import SessionLocal
from app.db.redis_client import client, QUEUE_KEY
from app.db.repository import get_event
from app.services.event import deliver_event

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
            deliver_event(db, event) # reuse the Delivery here
            print(f"Delivered event {event_id}, status={event.status}")
        except Exception as e:
            print(f"Error processing event {event_id}: {e}")
        finally: 
            db.close() # always release the connection


if __name__ == "__main__":
    run_worker()
from app.db.database import SessionLocal
from app.db.redis_client import client, QUEUE_KEY, enqueue_event, PROCESSING_KEY, schedule_retry, promote_due_retries
from app.db.repository import get_event, update_event_status, count_delivery_attempts
from app.services.event import deliver_event

MAX_ATTEMPTS = 5 # maximum retries const 
# recover the orphan ids that are stuck in the processing queue of the redis

def recover_orphans() -> None:
    """On startup, re-queue any ids stranded in the processing list by a previous crash."""

    while True:
        event_id = client.lmove(PROCESSING_KEY, QUEUE_KEY, src="LEFT", dest="RIGHT")
        if event_id is None: # processing list empty -> done
            break
        print(f"Recovered orphaned event {event_id} from processing")

def run_worker() -> None:
    print("Worker started, waiting for events...")
    recover_orphans() # reclaim anything a previous crash left
    
    
    while True:
        # block until an id shows up; returns(queue_name, value)
        
        event_id = client.blmove(QUEUE_KEY, PROCESSING_KEY, timeout=1, src="RIGHT", dest="LEFT") # src = RIGHT mimics the old BRPOPs and the dest = LEFT pushes it into the processing list
        promote_due_retries() #this is what moves the due retries back onto the main queue

        if event_id is None: 
            continue # idle seconds don't crash the worker
        event_id = int(event_id) # comes backs as string so to int

        db = SessionLocal() # our own session (no Depends here)

        try:
            event = get_event(db, event_id)
            if event is None:
                client.lrem(PROCESSING_KEY, 1, event_id) # ack before continue (nothing to recover for a ghost id)
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
                    delay = schedule_retry(event.id, attempts) # retry - status is still pending for these events until the maximum attempts are made
                    print(f"Event {event_id} failed (attempt {attempts}/{MAX_ATTEMPTS}), retry in {delay:.1f}s")
                else:
                    update_event_status(db, event.id, "failed") # maximum number of the attempts reached the event status saved as failed
                    print(f"Event {event_id} failed permanently after {attempts} attempts")

            # ack once handling complete - after the if and else block so it only runs if handling finished without an exception
            client.lrem(PROCESSING_KEY, 1, event_id) # ACK: handled -> remove from the processing
        except Exception as e:
            print(f"Error processing event {event_id}: {e}")
        finally: 
            db.close() # always release the connection


if __name__ == "__main__":
    run_worker()
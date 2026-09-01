from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.db.repository import get_stale_pending_events, update_event_status
from app.db.redis_client import enqueue_event, dead_letter

STALE_AFTER = timedelta(minutes=15)
EXPIRE_AFTER = timedelta(hours=24)
SWEEP_BATCH = 100

def sweep_pending_events(db: Session) -> tuple[list[int], list[int]]:
    """
    Requeue stranded pending events; dead-letter ones too old to deliver. 
    Returns (requeued_ids, expired_ids)
    """

    curr_time = datetime.now(timezone.utc)

    # 2 cutoffs, answering different questions
    # safety gate - is it safe to touch this event at all?
    stale_cutoff = curr_time - STALE_AFTER

    # a policy decision - is the webhook still worth sending
    expiry_cutoff = curr_time - EXPIRE_AFTER

    events = get_stale_pending_events(db, stale_cutoff, SWEEP_BATCH) # gets the batch of stale pending events from the db

    expired = []
    requeued = []

    for event in events:
        # events that are waiting for too long -> moves to the DLQ
        if event.created_at < expiry_cutoff:
            # expired event - send to dead-letter
            dead_letter(event.id)
            update_event_status(db, event.id, "dead")
            expired.append(event.id)
        else:
            enqueue_event(event.id) # the stale event - but it is still relevant - requeue
            requeued.append(event.id) 

    return requeued, expired





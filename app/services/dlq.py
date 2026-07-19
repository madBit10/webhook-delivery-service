from sqlalchemy.orm import Session
from app.db.redis_client import DEAD_KEY, QUEUE_KEY, client
from app.db.repository import update_event_status

def replay_dead_letters(db: Session) -> list[int]:
    replayed = []

    # looping the whole dead queue for the events 
    while True: 
        event_id = client.lmove(DEAD_KEY, QUEUE_KEY, src="RIGHT", dest="LEFT") # moving the events in the dead queue to the main queue
        if event_id is None: # check from the queue returns no event, if none is returned no need to check 
            break

        event_id = int(event_id)

        update_event_status(db, event_id, "pending") # status set to pending again if the event comes from the dead queue to the main queue
        replayed.append(event_id)
    return replayed



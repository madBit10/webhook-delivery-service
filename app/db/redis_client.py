import redis
from app.core.config import settings
import time, random

# importing the redis url from the .env that is added in the settings
REDIS_URL = settings.redis_url

# the queue key both sides agree on. Producer LPUSHes here, worker BRPOPs here. Making it a const means the route and the worker can never disagree on the name
QUEUE_KEY = "webhook:delivery"
PROCESSING_KEY = "webhook:processing" # BLMOVE pops from the main queue and pushes onto a processing list
DELAYED = "webhook:retries" # the ZSET (score = when to run)

BASE_DELAY = 1.0 # the start delay after what time each delivery will be scheduled if failed
MAX_DELAY = 60.0 # cap of the delay 

# creating a redis client 
client = redis.from_url(REDIS_URL, decode_responses=True)

def enqueue_event(event_id: int) -> None:
    """Push an event id onto the delivery queue (producer side)"""
    client.lpush(QUEUE_KEY, event_id)

# producer : schedule a job to run delay seconds from now

def schedule_retry(event_id: int, attempt_count: int) -> float:

    delay = min(MAX_DELAY, BASE_DELAY * (2 ** (attempt_count - 1))) # 1, 2, 4, 8, 16 (capped)

    delay = delay * (0.5 + random.random() * 0.5) # equal jitter

    client.zadd(DELAYED, {str(event_id) : time.time() + delay})

    return delay


# poller: move everything on due now to ready list

def promote_due_retries() -> None:
    now = time.time() # current time 
    due = client.zrangebyscore(DELAYED, "-inf", now) # all jobs whose deliver after has passed

    for event_id in due:
        if client.zrem(DELAYED, event_id):
            client.lpush(QUEUE_KEY, event_id)



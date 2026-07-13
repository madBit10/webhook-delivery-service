import redis
from app.core.config import settings

# importing the redis url from the .env that is added in the settings
REDIS_URL = settings.redis_url

# the queue key both sides agree on. Producer LPUSHes here, worker BRPOPs here. Making it a const means the route and the worker can never disagree on the name
QUEUE_KEY = "webhook:delivery"

# creating a redis client 
client = redis.from_url(REDIS_URL, decode_responses=True)

def enqueue_event(event_id: int) -> None:
    """Push an event id onto the delivery queue (producer side)"""
    client.lpush(QUEUE_KEY, event_id)


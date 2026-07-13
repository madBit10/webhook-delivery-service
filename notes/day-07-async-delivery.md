# 📘 Day 7 — Async Delivery (delivery leaves the request path)

> **What I built:** the producer/consumer split. Delivery used to run *inside* `POST /events`, so the
> client waited up to 5s. Now the API just **stores the event and drops its id on a Redis queue**, then
> returns `201` instantly. A **separate worker process** blocks on the queue, loads each event from the
> DB, and delivers it. Built the **plain queue first** (no retries yet) to prove the pipeline end to end
> before making it resilient.

---

## 🧠 1. Why async — the problem with sync-first

Day 6 delivered on the request thread:

```
client → route → emit_event (store pending) → deliver_event (httpx, up to 5s) → 201
```

The client waits for delivery. Slow receiver = slow API. 10,000 events = delivered one-at-a-time while
clients hang. And a failed delivery is just lost — nowhere to retry from.

The fix is the classic **producer / consumer** split:

```
POST /events → store event 'pending' → LPUSH id to Redis → return 201     (PRODUCER, fast)
worker (own process) → BRPOP id → load event from DB → deliver_event → update status   (CONSUMER, slow, off the request path)
```

## 🧩 2. Redis as a queue

Redis is an in-memory key/value store. I'm not using it as a cache here — I'm using one of its data
types, the **list**, as a queue. Two commands:

- `LPUSH queue id` — push onto the **left** (producer)
- `BRPOP queue` — **blocking** pop from the **right** (consumer). "Blocking" = if the list is empty, the
  worker **sleeps** until something arrives instead of busy-looping. Push left, pop right = **FIFO**.

Added a `redis:7` container to docker-compose (port `6379` exposed so my venv app + worker reach it at
`localhost:6379`), and the `redis==5.2.1` Python client.

## ⚙️ 3. Two key design decisions

- **The queue carries only the event `id` (an int), not the event object.** The database is the source
  of truth; the queue is just a pointer — "event #5 needs delivering." The worker reloads the fresh row.
  Smaller messages, no stale data, DB stays authoritative.
- **Store in Postgres BEFORE enqueueing.** The route commits the `pending` row first, *then* pushes to
  Redis. If it crashed between the two, the row still exists as `pending` and can be recovered by a
  future sweep. The reverse order (enqueue then commit) could hand the worker an id that isn't in the DB
  yet. This is the Day-5 "store before you act" principle paying off.

## 🔌 4. The client module — `app/db/redis_client.py`

```python
import redis
from app.core.config import settings

QUEUE_KEY = "webhook:delivery"   # producer + worker agree on this name → no typo drift

client = redis.from_url(settings.redis_url, decode_responses=True)

def enqueue_event(event_id: int) -> None:
    client.lpush(QUEUE_KEY, event_id)
```

- **One module-level `client`** — created once at import, connection pool reused everywhere. Same "one
  engine" idea as `database.py`.
- `decode_responses=True` — without it, Redis hands back raw **bytes** (`b"5"`); with it, **str** (`"5"`).
- `redis_url` comes from config (`.env` → `REDIS_URL`), same pattern as the Postgres URL.

## 📤 5. Producer — the route change

```python
event = emit_event(db, data)          # store as pending
if event is None:
    raise HTTPException(404, "Endpoint not found")
enqueue_event(event.id)               # hand off to the worker; don't wait
return event                          # the stored 'pending' event
```

The **mental shift**: Day 6 returned the event *after* delivery (status `delivered`/`failed`). Now it
returns *before* delivery even starts — so the response shows **`status: "pending"`**, and `201` comes
back instantly. The API's job is now just "accepted & stored"; the worker handles delivery afterward.

## 🏃 6. Consumer — `app/worker.py` (a separate process)

```python
from app.db.database import SessionLocal
from app.db.redis_client import client, QUEUE_KEY
from app.db.repository import get_event
from app.services.event import deliver_event

def run_worker() -> None:
    print("Worker started, waiting for events...")
    while True:
        _, event_id = client.brpop(QUEUE_KEY, timeout=0)   # blocks; returns (queue, value)
        event_id = int(event_id)                            # comes back as a string
        db = SessionLocal()                                 # own session — no Depends outside a request
        try:
            event = get_event(db, event_id)
            if event is None:
                print(f"Event {event_id} not found, skipping")
                continue
            deliver_event(db, event)                        # reuse Day 6 delivery, unchanged
            print(f"Delivered event {event_id}, status={event.status}")
        finally:
            db.close()

if __name__ == "__main__":
    run_worker()
```

Run with `python -m app.worker` in a second terminal.

- **The worker can't use `Depends(get_db)`** — that's FastAPI dependency injection, only works inside a
  request. The worker has no request, so it makes its own session from `SessionLocal()` and is
  responsible for closing it (`try/finally`).
- **Reused `deliver_event` unchanged** — the whole delivery engine from Day 6 dropped straight in. The
  worker just loads the event and hands it off. Added a small `get_event(db, id)` repo fn (mirrors
  `get_endpoint`).

## 🐛 7. Bugs / gotchas caught

- **`decode_responses` is PLURAL** — with an `s`. `decode_response` = unknown kwarg → crash on import.
- **`.env` typo `REDIS_UTR`** — since the config field has no default it's *required*, so the misspelling
  meant pydantic couldn't find it → "field required" crash at startup.
- **Route must `return event`, not `return enqueue_event(...)`** — `enqueue_event` returns `None`, and
  `None` against `response_model=EventRead` → 500.
- **`brpop` returns a TUPLE** `(queue_name, value)`, not just the value → unpack `_, event_id`. And
  `value` is a **str** → `int()` it.
- **Don't name the module `redis.py`** — it shadows the `redis` package. Used `redis_client.py`.
- **Type-checker: "Awaitable not iterable" on the brpop unpack** — false alarm. The stubs say `brpop`
  *might* return an Awaitable (the async client shares the method name); the sync client returns a real
  list at runtime. Silence with `# type: ignore[misc]` if desired.
- Pinned **`httpx==0.28.1`** — Day-6 imported it but it was never in `requirements.txt` (a fresh install
  or Docker build would've broken).

## ✅ 8. Verified end to end

Worker running in a 2nd terminal. POST a new event → API returns `201` / `status: "pending"` instantly →
worker prints `Delivered event N, status=delivered` within a second. Event 5 drained from a pre-seeded
queue; event 6 delivered **live**. DB confirms `delivered` + a `DeliveryAttempt` row (`success=t`, `200`).

## ⚠️ 9. Known-fragile (fixed in Phase 7)

- If `deliver_event` **raises**, the worker loop crashes and the worker dies — no `try/except` around
  delivery yet.
- **No retries** — a `failed` delivery just stays failed.
- `enqueue_event` raises if Redis is down — acceptable for now (the event is already stored `pending`,
  and a `500` tells the client to retry).

## ⏭️ Next — Phase 7: retries + resilience

Retry on `failed` with **exponential backoff** (1, 2, 4, 8s) + **jitter** (avoid the thundering herd);
add `attempt_number` / `duration_ms` columns (new migration); a **dead-letter queue** to park events
after N failed tries; wrap the worker's delivery in `try/except` so it survives a raise.

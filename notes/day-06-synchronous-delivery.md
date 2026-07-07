# 📘 Day 6 — Synchronous Delivery (the system finally *delivers*)

> **What I built:** the delivery engine. A `DeliveryAttempt` table (2nd foreign key), the repository
> UPDATE pattern, and the `deliver_event` service that makes my app's **first outbound HTTP call** —
> POSTing an event's payload to the subscriber's URL, logging the attempt, and flipping the event's
> status. Built **synchronously first** (deliver right inside `POST /events`) to prove the logic end
> to end before making it async.

---

## 🧱 1. `DeliveryAttempt` model — one row per attempt

One-to-many, one level down the chain: `endpoints → events → delivery_attempts`.

```python
class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"          # plural, consistent with endpoints/events
    id                   = Column(Integer, primary_key=True)   # PK auto-indexed → no index=True
    event_id             = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    attempted_at         = Column(DateTime(timezone=True), nullable=False,
                                  default=lambda: datetime.now(timezone.utc))
    success              = Column(Boolean, nullable=False)
    response_status_code = Column(Integer, nullable=True)   # NULL = no response at all
    response_body        = Column(String,  nullable=True)   # body OR error text
```

- **The nullable columns are the whole design insight.** Failure modes differ:
  - receiver responded with an error → `status_code = 500`, body = their error
  - couldn't reach receiver at all → `status_code = NULL`, body = the exception text
  - `NULL` vs a value is itself information: *"did we get any response?"*
- `\d delivery_attempts` confirmed the FK constraint + `ix_delivery_attempts_event_id`. The `id`
  Default shows `nextval(...seq)` = **server-side** default (Postgres sequence), vs `attempted_at`/
  `success` blank = **app-side** (my `lambda`/code). Saw both kinds of default side by side.

## 🗃️ 2. Repository — INSERT vs UPDATE (the new pattern)

- `create_delivery_attempt(...)` → same INSERT shape as `create_event` (add → commit → refresh +
  `try/except SQLAlchemyError: rollback; raise`). Omit `attempted_at` (lambda default fills it).
  Type hints `Optional[int]`/`Optional[str]` to match the nullable columns (they'll be `None` on timeout).
- `update_event_status(...)` → **first UPDATE**. The ORM pattern is: **fetch → mutate → commit.**
  ```python
  event = db.query(Event).filter(Event.id == event_id).first()
  event.status = new_status      # reassign attribute on the (mutable) event object
  db.commit()                    # dirty tracking notices → emits UPDATE. NO db.add() needed!
  ```
  - **Lesson:** `add()` is for NEW rows (INSERT). A row loaded via query is already in the session, so
    mutate + commit is enough → **dirty tracking** auto-generates the UPDATE. `add()` here is a no-op.
  - **Lesson (Python):** strings are immutable, but `event.status = x` isn't mutating a string — it's
    **reassigning the attribute** to a new string. The `event` object is mutable. Reassigning a name to
    a new object is always allowed even for immutable values (`x = 5; x = 6`).

## ⚙️ 3. `deliver_event` — the outbound call & the 3 outcomes

```python
def deliver_event(db, event) -> Event:
    endpoint = get_endpoint_repo(db, event.endpoint_id)   # event has only endpoint_id (FK) → resolve URL
    url = endpoint.url
    try:
        response = httpx.post(url, json=event.payload, timeout=5.0)
        success = 200 <= response.status_code < 300        # 2xx = delivered
        status_code = response.status_code
        body = response.text
    except httpx.RequestError as e:                        # timeout / refused / DNS = unreachable
        success, status_code, body = False, None, str(e)

    body = body[:1000]                                     # truncate — receivers can return huge bodies
    create_delivery_attempt(db, event.id, success, status_code, body)
    new_status = "delivered" if success else "failed"      # ← ternary, NOT `"delivered" or "failed"`
    return update_event_status(db, event.id, new_status)
```

- **First outbound HTTP** — my app is now a *client*, not just a server. `httpx.post(url, json=..., timeout=...)`.
  - `json=` (not `data=`) → serializes dict to JSON + sets `Content-Type: application/json`.
  - `timeout=5.0` is **mandatory** — the whole point of a reliable delivery system; bounds a hanging receiver.
- **The event carries `endpoint_id`, not the URL** → must look up the endpoint to resolve `.url` (follow the FK).
- 🐛 **`"delivered" or "failed"` bug:** `or` returns the first *truthy* operand → always `"delivered"`.
  Every failure would be silently marked delivered. Fix = ternary `"delivered" if success else "failed"`.
  Rule: `A or B` picks first-truthy; `A if cond else B` picks by condition.

## 🔌 4. Wired into `POST /events` (sync-first) + an API-design call

```python
event = emit_event(db, data)                # store as "pending"
if event is None: raise HTTPException(404, "Endpoint not found")
delivered = deliver_event(db, event)        # deliver NOW (sync) — status → delivered/failed
return delivered
```

- **Trade-off (intentional, temporary):** calling `deliver_event` inside the request **breaks the
  "respond immediately" half** of the store-first principle — the producer now waits up to 5s. Accepted
  for now to *see* delivery work; async worker later moves this off the request path.
- **API-design lesson:** return **201 even if delivery FAILS.** `POST /events` means "did we accept +
  store your event?" — not "did we deliver?". Delivery failure isn't the client's fault (not 4xx) and the
  event isn't lost (not 5xx). Producer gets 201; the `status` field reports the delivery outcome.

## 🔐 5. Aside — why the endpoint `secret` can't be hashed
The HMAC signing secret must be **read back** to sign payloads, so it can't be one-way hashed like a
password (passwords are only ever *verified*, never reproduced). Protect a must-read secret with
**encryption at rest** (key kept OUT of the DB) or a secrets manager/KMS — not hashing. *(Roadmap:
encrypt endpoint secret with Fernet, key in `.env`.)*

## ✅ 6. Verified end to end (3 attempt types)
```
id | event_id | success | response_status_code | body_preview
 1 |    2     |   t     |        200           | { "args": {}, "data": ... }   ← delivered (httpbin/post)
 2 |    3     |   f     |        500           | (empty)                       ← rejected  (httpbin/status/500)
 3 |    4     |   f     |       NULL           | The read operation timed out  ← unreachable (httpbin/delay/10, 5s timeout)
```

## 🫏 7. Donkey translation
| 🫏 Donkey | 🛠️ Technical |
|---|---|
| Walk the letter to the farm's door | outbound `httpx.post` to endpoint URL |
| Read which farm from the address book | resolve `endpoint_id` → `endpoint.url` |
| Farm took the letter | 2xx → success, `delivered` |
| Farm slammed the door | non-2xx (500) → failed, status code present |
| Nobody home after 5s | `RequestError` → failed, status code `NULL` |
| Logbook of every trip | `delivery_attempts` rows |
| Swap the "pending"/"delivered" tag | `update_event_status` (dirty tracking UPDATE) |
| Deliver while you wait vs send a courier | synchronous (now) vs async worker (next) |

## ✅ Status & next
**Day 6 complete — synchronous delivery built & verified (all 3 outcomes).**
**Next — make delivery ASYNC + resilient:**
- Redis queue + a **worker** that picks up `pending` events and calls `deliver_event` (moves it off the
  request path → restores "respond immediately").
- **Retries** with exponential backoff + jitter; add `attempt_number` (+ maybe `duration_ms`) columns.
- Permanent failures → **dead-letter queue** (replayable).
- Then: **HMAC-sign** the payload with the endpoint secret (finally uses `secret`); auth; rate limiting.
- Later reads: `GET /events`, `GET /events/{id}`.

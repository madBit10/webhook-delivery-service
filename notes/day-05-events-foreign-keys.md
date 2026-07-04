# 📘 Day 5 — Events + Foreign Keys (`POST /events`)

> **What I built:** the heart of the system — **events**. The `Event` model (with my first
> **foreign key** linking an event to an endpoint), an Alembic migration, and the full slice to
> **emit** an event: schema → repository → service → route. The defining reliability principle of
> the whole project shows up here: **an event is durably stored as `pending` BEFORE any delivery
> is attempted.**

---

## 📨 1. What an Event Is (and why it's the core)

- **Endpoint** = the *address* (Farm B's door / a subscriber URL). Config.
- **Event** = the *news that something happened* ("carrot is ready!") — a single fact to deliver.

**The principle that defines this project:**
> **Store the event first (durably), then attempt delivery separately.**

Why: delivery is unreliable (receiver may be down). If you POST first and only save on success, a
crash mid-delivery loses the event → broken promise. Instead: write to Postgres immediately (safe
forever), and a worker attempts delivery *later*, retrying as needed. The DB is the durable record;
delivery is a separate, retryable step. **This is what makes the system "reliable."**

> 🫏 The **URL is the address on the envelope**; the **payload is the letter inside**. Store the
> letter now; a donkey carries the envelope to the address later.

---

## 🔗 2. Foreign Keys — My First Relationship

An event doesn't exist in a vacuum — **every event is *for* one endpoint.** That "belongs to" link
is a **foreign key**.

```python
from sqlalchemy import ForeignKey
endpoint_id = Column(Integer, ForeignKey("endpoints.id"), nullable=False, index=True)
```

- `ForeignKey("endpoints.id")` → references the **table name** `endpoints` + column `id` (not the
  Python class `Endpoint`). The DB **rejects** any event pointing to a non-existent endpoint —
  *referential integrity enforced at the database level.*
- **One-to-many:** one endpoint has many events; each event belongs to one endpoint.
- **`index=True` on the FK** — I'll constantly query "all events for endpoint X" (`WHERE endpoint_id
  = 5`). Postgres does **not** auto-index foreign keys (it *does* auto-index primary keys), so I add
  it. **Rule: index columns you filter or join on.**

```
endpoints                 events
id (PK) ◀───────────────  endpoint_id (FK → endpoints.id)
url                       id (PK), event_type, payload, status, created_at
```

---

## 🧱 3. The `Event` Model — Column Decisions

```python
class Event(Base):
    __tablename__ = "events"
    id          = Column(Integer, primary_key=True, index=True)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"), nullable=False, index=True)
    event_type  = Column(String, nullable=False)
    payload     = Column(JSON, nullable=False)
    status      = Column(String, nullable=False, default="pending")
    created_at  = Column(DateTime(timezone=True),
                         default=lambda: datetime.now(timezone.utc), nullable=False)
```

- **`event_type` singular** (vs the endpoint's plural `event_types`). An endpoint *subscribes to
  many* types (CSV); an event *is exactly one* type. Same word, different cardinality.
- **`payload` = `JSON`**, not `String`. Postgres validates it's real JSON and lets me query into it
  later (`payload->>'order_id'`). A plain string could hold `"not json"` and I'd never know.
  (`JSONB` is even better — binary/indexable — for later.)
- **`status` default `"pending"`** — every event is born undelivered; a worker later flips it to
  `delivered`/`failed`. The seed of a **state machine**.
- **`created_at` with a `lambda` default** — the lambda matters: without it,
  `datetime.now(timezone.utc)` would evaluate **once at import** and every row would share that
  timestamp. The lambda defers evaluation to insert-time. `timezone=True` → store tz-aware UTC.
- **Rule:** any table recording "things that happened" wants a `created_at`. Config tables (endpoints)
  can skip it; fact tables (events) shouldn't.

---

## 🗃️ 4. The Migration (and reading `\d events`)

```bash
alembic revision --autogenerate -m "create events table"   # REVIEW the file, then:
alembic upgrade head
docker compose exec db psql -U example -d exampledb -c "\d events"
```

Verified in `\d events`:
- `Foreign-key constraints: events_endpoint_id_fkey FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)` ✅
- `ix_events_endpoint_id btree (endpoint_id)` ✅ (my FK index)
- `status` / `created_at` have **blank** Default columns → **expected**: `default=` is filled by the
  **ORM**, not the DB, so app-side defaults don't appear in `\d` (same lesson as Day 2's `is_active`).

**Learned:** I had `index=True` on the `id` PK → produced a redundant `ix_events_id` on top of the
auto-created `events_pkey`. Harmless, kept for consistency, but **PKs are auto-indexed → `index=True`
on a PK is redundant.**

---

## 🧬 5. Schemas — Input/Output for Events

```python
class EventCreate(BaseModel):                    # INPUT — only what the client controls
    endpoint_id: int
    event_type: str
    payload: dict[str, Any]

class EventRead(BaseModel):                      # OUTPUT — the full stored picture
    id: int
    endpoint_id: int
    event_type: str
    payload: dict[str, Any]
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
```

- **`EventCreate` omits `id`, `status`, `created_at`** — the client only sends what it's allowed to
  control (same principle as `EndpointCreate` omitting `secret`). Status is server-set to `pending`.
- **`payload: dict[str, Any]`** — a JSON object: string keys, any values.
- **🐛 Two bugs I hit in `EventRead`:**
  1. `status: int` → wrong; it's text (`"pending"`). Must be `str`.
  2. `create_at` (typo) → **500**. With `from_attributes=True`, Pydantic maps each field **by name**
     to the ORM attribute (`event.created_at`). A misnamed field finds no attribute → required-field
     error → 500. **Read-schema field names MUST exactly match the model's column names.**
- **`datetime`** is the right type for a timestamp — not `date` (day only) or `time` (clock only).

---

## ⚙️ 6. Repo → Service → Route

**Repository — `create_event`** (same `add/commit/refresh` + rollback as `create_endpoint`):
```python
event = Event(endpoint_id=..., event_type=..., payload=...)   # NO status → defaults "pending"
try:
    db.add(event); db.commit(); db.refresh(event)
    return event
except SQLAlchemyError:
    db.rollback(); raise
```

**Service — `emit_event`** (new file `app/services/event.py`), holds the business rule:
```python
def emit_event(db, data: EventCreate) -> Optional[Event]:
    endpoint = get_endpoint_repo(db, data.endpoint_id)   # does the endpoint exist?
    if endpoint is None:
        return None                                       # signal "not found"
    return create_event(db, data.endpoint_id, data.event_type, data.payload)
```

**Route — `POST /events`** (new file `app/api/routes/event.py`, wired into `main.py`):
```python
@router.post("/events", response_model=EventRead, status_code=201)
def emit_event_route(data: EventCreate, db: Session = Depends(get_db)):
    event = emit_event(db, data)
    if event is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return event
```

### 🔑 The key design lesson: validate in the app layer, keep the FK as a safety net
The DB foreign key *would* reject a bad `endpoint_id` — but that surfaces as an `IntegrityError` →
caught by rollback → re-raised → **500**. A bad `endpoint_id` is the **client's** fault → should be a
**4xx**, not a 500. So the **service checks existence first** and returns `None` → router → clean
**404**. The FK stays as **defense in depth** (still guarantees integrity if something slips through).

> 🎤 *"Rely on DB constraints for integrity, but validate in the app layer for good error messages and
> correct status codes. A foreign-key violation surfacing as a 500 is a bad API."*

**Layering note:** the service returns `None` (not `HTTPException`) — it stays HTTP-agnostic; the
**router** owns the HTTP status. Chose `None`-signal (Option A, consistent with `get_endpoint`) over a
custom exception (Option B, cleaner but more machinery) for now.

---

## ✅ 7. Verified End to End
1. `POST /events` with a real `endpoint_id` → **201**, `status: "pending"`, real `created_at` ✅
2. `POST /events` with `endpoint_id: 9999` → **404** "Endpoint not found" (not a 500) ✅
3. `psql SELECT ... FROM events` → row persisted with `status = pending` — the durable record before
   any delivery ✅

---

## 🫏 8. Donkey Translation (this day)

| 🫏 Donkey | 🛠️ Technical term |
|-----------|-------------------|
| "Carrot is ready!" news for a farm | an **event** (for an endpoint) |
| Which farm the news is for | `endpoint_id` **foreign key** |
| The letter inside the envelope | `payload` (JSON) |
| File the letter now, deliver later | store `pending` before delivery |
| "No such farm" at the counter | app-layer 404 (not raw DB 500) |
| Rule that the farm must exist | foreign-key constraint (safety net) |
| "not delivered yet" stamp | `status = "pending"` |

---

## ✅ Status & next

**Day 5 complete — event emission built, verified, merged.**
1. ✅ `Event` model + first foreign key (event → endpoint) + index on the FK
2. ✅ Alembic migration for `events`
3. ✅ Schemas / repo `create_event` / service `emit_event` / route `POST /events`
4. ✅ Store-first (`pending`) + app-layer validation over the FK

**Next — the real payoff: ASYNC DELIVERY.**
- A `delivery_attempts` table (log every try) — another foreign key.
- A **worker** (Redis-backed) that picks up `pending` events and POSTs the payload to the endpoint URL.
- **HMAC-sign** the payload with the endpoint's `secret` (finally using it!).
- **Retries with exponential backoff + jitter**; permanent failures → **dead-letter queue**.
- Flip `status`: `pending → delivered / failed` (the state machine).
- Later reads: `GET /events`, `GET /events/{id}`.

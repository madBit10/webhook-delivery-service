# 🏛️ Architecture — Webhook Delivery Service

System-design view of the project. Four complementary diagrams — each answers a different question:
1. **Component diagram** — *what are the moving parts?*
2. **Layered architecture** — *how is the code organized inside the API?*
3. **ER diagram** — *how is the data related?*
4. **Data-flow / sequence** — *what happens to one event, end to end?*

---

## 1. Component diagram (high level — the boxes & arrows)

Shows the major services and how they talk. Arrows = "sends data to".

```
                          WEBHOOK DELIVERY SERVICE
┌────────────┐                                                    ┌──────────────┐
│  PRODUCER  │   POST /endpoints                                  │  SUBSCRIBER  │
│  (client:  │   POST /events                                     │  servers     │
│   Stripe-  │ ─────────────────►┌─────────────────┐             │ (receiver    │
│   like app)│                   │   FastAPI  API   │             │  URLs)       │
└────────────┘                   │  (sync request  │             └──────▲───────┘
                                 │     path)       │                    │
                                 └───────┬─────────┘                    │ POST payload
                                         │  store event = "pending"     │ (+HMAC sig)
                                         ▼                              │
                                 ┌─────────────────┐                    │
                                 │   PostgreSQL    │                    │
                                 │  endpoints      │            ┌───────┴────────┐
                                 │  events         │◄──update── │    DELIVERY    │
                                 │  delivery_      │   status   │    WORKER      │
                                 │    attempts     │──"pending"─►│  (async, from  │
                                 └─────────────────┘   events   │  a queue)      │
                                         ▲                      └───────┬────────┘
                                         │ enqueue event id             │ retry w/
                                         ▼                              │ backoff
                                 ┌─────────────────┐                    ▼
                                 │  Redis  (queue) │            ┌──────────────┐
                                 │  + retry/DLQ    │            │ Dead-Letter  │
                                 └─────────────────┘            │ Queue (DLQ)  │
                                                                └──────────────┘
```

---

## 2. Layered architecture (inside the FastAPI box)

Strict one-way dependency; only the repository imports SQLAlchemy.

```
   HTTP request
        │
        ▼
┌─────────────────┐   validates I/O (Pydantic schemas), owns HTTP status codes
│   ROUTE (API)   │   e.g. POST /events, POST /endpoints
└───────┬─────────┘
        ▼
┌─────────────────┐   business rules, HTTP-agnostic
│    SERVICE      │   e.g. emit_event(), deliver_event()  (returns None/objects, not HTTPException)
└───────┬─────────┘
        ▼
┌─────────────────┐   data access only — the ONLY layer that imports SQLAlchemy
│   REPOSITORY    │   e.g. create_event(), create_delivery_attempt(), update_event_status()
└───────┬─────────┘
        ▼
┌─────────────────┐
│    DATABASE     │   PostgreSQL (SQLAlchemy models + Alembic migrations)
└─────────────────┘
```

---

## 3. ER diagram (data model + relationships)

`1 ── *` = one-to-many. `PK` = primary key, `FK` = foreign key.

```
┌────────────┐        ┌────────────┐        ┌────────────────────┐
│ endpoints  │ 1    * │  events    │ 1    * │ delivery_attempts  │
│────────────│────────│────────────│────────│────────────────────│
│ id (PK)    │◄──┐    │ id (PK)    │◄──┐    │ id (PK)            │
│ url        │   └────│ endpoint_id│   └────│ event_id (FK)      │
│ secret     │  (FK)  │ event_type │  (FK)  │ attempted_at       │
│ event_types│        │ payload    │        │ success            │
│ is_active  │        │ status ────┼──┐     │ response_status_   │
└────────────┘        │ created_at │  │     │   code (nullable)  │
                      └────────────┘  │     │ response_body      │
                                      │     └────────────────────┘
                            event.status state machine:
                     pending → delivered / failed → (retry → DLQ)
```

---

## 4. Data-flow / sequence (one event, end to end)

The "money path" — the reliability guarantee in action.

```
 ① Producer POST /events
        │
        ▼
 ② Store event as "pending"  ──────────────►  [Postgres]   ◄── durable record, survives crashes
        │                                          │
        │  (respond 201 to producer immediately)   │
        ▼                                          │
 ③ Enqueue event id  ──────────────────────►  [Redis queue]
        │
        ▼
 ④ Worker pops event  ◄─────────────────────  [Redis queue]
        │
        ▼
 ⑤ POST payload (HMAC-signed) to endpoint URL  ──►  [Subscriber]
        │
        ├── 2xx  → log attempt(success) → status = "delivered"  ✅ done
        │
        └── fail → log attempt(failure) → retry w/ exponential backoff + jitter
                         │
                         └── after N tries → move to Dead-Letter Queue  ☠️ (replayable)
```

---

## Progress map

```
✅ BUILT (Days 1–5)          🚧 NOW (Day 6)             ⬜ NEXT
─────────────────────        ──────────────────         ─────────────────────
Route→Service→Repo→DB        delivery_attempts table    Redis queue + worker
endpoints CRUD               sync deliver_event()       retries/backoff/DLQ
events (store "pending")     httpx outbound POST        HMAC sign + verify
FK relationships             flip event status          auth, rate limit
Docker + Postgres                                       CI/CD, Terraform, AWS
```

**Sync-first note:** today we build the sync version of ④–⑤ (skip the queue — call `deliver_event`
directly from `POST /events`), then later lift it onto the Redis worker.

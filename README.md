# Webhook Delivery Service

A reliable webhook delivery platform — the infrastructure layer that guarantees an event *eventually* reaches its subscriber, even when the receiver is down, slow, or failing. Think of the delivery system that powers Stripe and GitHub webhooks.

> **Status:** 🚧 In active development — built as a deep-dive into production-grade Python backend + DevOps.

## The Problem

When something happens in one system (a payment succeeds, a build finishes), other systems need to know. Polling (*"anything new yet?"*) is wasteful, so the producer **POSTs to a URL** the subscriber registered — a webhook. The hard part is **delivery isn't guaranteed**: receivers go down, time out, or return errors. A naive "fire and forget" loses events silently. This service is the reliable delivery layer that solves that.

## What It Does

- Producers **register endpoints** (subscriber URLs) and **emit events**
- Every event is **durably stored** before any delivery is attempted
- Delivery runs on **async background workers** (no blocking the producer)
- Failed deliveries **retry with exponential backoff + jitter**
- Permanently-failing events move to a **dead-letter queue**, replayable on demand
- Duplicate deliveries are made **safe** — a stable idempotency key + terminal-status guard (at-least-once, deduped)
- Every delivery is **HMAC-signed** over the exact bytes sent, with a timestamp to block replays — receivers can prove it came from us and wasn't tampered with
- **Every delivery attempt is logged** for full observability

## Tech Stack

| Layer | Tech |
|-------|------|
| API | FastAPI + Pydantic |
| Persistence | PostgreSQL + SQLAlchemy + Alembic |
| Async / queue | Redis + workers |
| Frontend | Next.js (App Router) + TypeScript + Tailwind — a dashboard in `frontend/` |
| Testing | pytest + httpx TestClient |
| DevOps | Docker, docker-compose, GitHub Actions CI/CD, Terraform, AWS, Prometheus + Grafana |

## Architecture

Layered, with strict separation of concerns:

```
HTTP → Router → Service → Repository → Database
       (API)    (logic)   (data access)
```

## Getting Started

```bash
# 1. create + activate a virtualenv, then install deps
pip install -r requirements.txt

# 2. create a .env with: DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD,
#    POSTGRES_DB, REDIS_URL (e.g. redis://localhost:6379/0)
#    then start Postgres + Redis
docker compose up -d

# 3. apply database migrations
alembic upgrade head

# 4. run the app
uvicorn app.main:app --reload

# 5. in a SECOND terminal, run the delivery worker
python -m app.worker
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger): http://127.0.0.1:8000/docs

> The **worker** is a separate process from the API. The API stores events and
> queues them; the worker drains the queue and performs delivery. Run both.

### Frontend dashboard (optional)

A Next.js dashboard lives in `frontend/`. It calls the API (CORS is enabled for
`http://localhost:3000`), so run the backend first, then:

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

Three pages, wired together with shared nav:

- **Emit & watch** (`/`) — posts an event and polls its status live (`pending → delivered / dead`).
- **Events** (`/events`) — lists all events with color-coded status badges.
- **Dead-letter queue** (`/dlq`) — shows dead-lettered events with a **Replay** button (`POST /dlq/replay`).

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check → `{"status": "ok"}` |
| `POST` | `/endpoints` | Register a webhook endpoint |
| `GET` | `/endpoints` | List endpoints (paginated: `?skip=&limit=`) |
| `GET` | `/endpoints/{id}` | Get one endpoint (404 if missing) |
| `POST` | `/events` | Emit an event for an endpoint |
| `POST` | `/dlq/replay` | Re-drive all dead-lettered events back onto the queue → `{replayed, count}` |

### `POST /endpoints`

Registers a subscriber URL. The server generates a cryptographically-secure HMAC signing
secret (`secrets.token_hex`) and stores it — the secret is **never returned** in any response.
The `url` is validated as a real URL (`HttpUrl`) on input.

**Request**
```json
{
  "url": "https://example.com/webhook",
  "event_types": "order.created"
}
```

**Response** `201 Created`
```json
{
  "id": 1,
  "url": "https://example.com/webhook",
  "event_types": "order.created",
  "is_active": true
}
```

### `POST /events`

Emits an event for a registered endpoint. The event is **durably stored with `status: "pending"`**,
its id is **pushed onto a Redis queue**, and the endpoint returns **immediately** — delivery happens
**asynchronously** on a separate worker process, off the request path. The response therefore reflects
`status: "pending"` (delivery hasn't happened yet).

A background **worker** (`python -m app.worker`) pulls each id off the queue, loads the event from the DB,
POSTs the payload to the endpoint's URL (`httpx`, 5s timeout) — **signed with that endpoint's secret**
(see [Verifying webhook signatures](#verifying-webhook-signatures)) — and records the outcome in
`delivery_attempts`. On failure it **retries up to 5 times with exponential backoff + jitter** — delays
double each attempt (~1, 2, 4, 8s, randomized) via a Redis sorted-set scheduled queue, so a struggling
endpoint gets increasing breathing room instead of being hammered. `status` stays `pending` between
tries; only once retries are exhausted is the event **dead-lettered** — pushed onto a separate
`webhook:dead` list and marked terminal `dead` (success → `delivered`). Dead events aren't retried
automatically; `POST /dlq/replay` re-drives them back onto the queue (e.g. after the receiver is fixed) —
the re-attempt itself is the "is it back up?" check.

Delivery uses a **reliable queue**: the worker atomically moves each id to an in-flight `processing` list
(`BLMOVE`) and only removes it after handling (`LREM`). If the worker crashes mid-delivery, the id
survives and is re-queued on the next startup — so a hard crash can't orphan an event.

Because delivery is **at-least-once**, the same event can be delivered more than once (e.g. the worker
crashes *after* the POST but *before* recording the status, so recovery re-queues it). The service makes
duplicates **safe** two ways: (1) before delivering, the worker **skips any event already in a terminal
state** (`delivered`/`dead`), so a re-queued finished event is acked and dropped instead of re-sent; and
(2) every outbound POST carries a stable **`X-Idempotency-Key: <event_id>`** header so the receiver can
deduplicate the one case the sender can't prevent. The key is the event id (constant across all retries),
not the attempt number. Exactly-once is impossible over a network — **at-least-once + idempotency** yields
an exactly-once *effect*.

Returns `201` — meaning the event was *accepted and stored*, not that it was delivered. If the referenced
`endpoint_id` doesn't exist, returns `404` (validated in the app layer; the DB foreign key is a safety net).

**Request**
```json
{
  "endpoint_id": 1,
  "event_type": "order.created",
  "payload": { "order_id": 12345, "amount": 99.50 }
}
```

**Response** `201 Created`
```json
{
  "id": 1,
  "endpoint_id": 1,
  "event_type": "order.created",
  "payload": { "order_id": 12345, "amount": 99.50 },
  "status": "pending",
  "created_at": "2026-07-13T10:00:00+00:00"
}
```

**Delivery outcomes** — every attempt is logged in `delivery_attempts`:

| Outcome | `success` | `response_status_code` | Event `status` |
|---|---|---|---|
| Receiver returns `2xx` | `true` | e.g. `200` | `delivered` |
| Receiver returns non-`2xx` | `false` | e.g. `500` | `pending` → retry, then `dead` (dead-lettered) after 5 tries |
| Receiver unreachable / timeout | `false` | `NULL` (no response) | `pending` → retry, then `dead` (dead-lettered) after 5 tries |

Each attempt row also records **`attempt_number`** (which try this was — 1, 2, 3…) and **`duration_ms`**
(how long the outbound POST took, for observability). The **worker is crash-hardened**: an unexpected
error on one event is logged and skipped, so it can never take down delivery for the rest of the queue.

## Verifying webhook signatures

Your endpoint is a URL on the public internet — anyone who discovers it can POST to it, and HTTPS won't
tell you who sent what (TLS secures the *channel*, not the *sender*). So every delivery is **signed** with
the secret generated when you registered the endpoint.

**Always verify the signature before acting on a webhook.**

### Headers on every delivery

| Header | Meaning |
|--------|---------|
| `X-Webhook-Signature` | HMAC-SHA256 of the signed message, hex-encoded (64 chars) |
| `X-Webhook-Timestamp` | Unix seconds when the request was signed |
| `X-Idempotency-Key` | The event id — stable across all retries, use it to deduplicate |
| `Content-Type` | `application/json` |

### The signed message

```
signature = HMAC-SHA256(key = <your endpoint secret>, message = "<timestamp>.<raw request body>")
```

The timestamp is *inside* the signed message, so it can't be altered without breaking the signature.

### Verifying it

```python
import hmac, hashlib, time

def verify(secret: str, raw_body: bytes, timestamp: str, signature: str) -> bool:
    # reject replays: anything more than 5 minutes off, in either direction
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False

    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
```

A complete runnable receiver — the one used to verify this implementation end-to-end — lives in
[`examples/verify_receiver.py`](examples/verify_receiver.py).

### Three things that will bite you

- **Hash the RAW request body, never a re-serialized version.** JSON isn't canonical — parsing the body and
  dumping it again produces different bytes (different key order, different whitespace) and the signature
  will never match. In FastAPI use `await request.body()`, not `await request.json()`.
- **Compare with `hmac.compare_digest`, never `==`.** String equality short-circuits on the first differing
  byte, so response timing leaks the signature one byte at a time. `compare_digest` is constant-time.
- **Check the timestamp.** A signature is valid forever, so without a freshness window a captured request can
  be replayed indefinitely — every copy with a perfectly valid signature.

Reject with `401` and return the *same* error for every failure mode. Telling the caller which check failed
lets an attacker probe your validation one condition at a time.

### Getting your secret

The secret is generated with `secrets.token_hex(32)` at registration and is **never returned by the API** —
not on create, not on read. Each endpoint gets its own. In this project it's read straight from the database:

```bash
docker compose exec db psql -U example -d exampledb \
  -c "SELECT id, url, secret FROM endpoints ORDER BY id DESC LIMIT 1;"
```

## Roadmap

- [x] Layered FastAPI scaffold + `/health` endpoint
- [x] Dockerize app + Postgres + Redis via docker-compose
- [x] Data layer — SQLAlchemy engine/session, `Endpoint` model, Alembic migrations
- [x] Endpoint registration — `POST /endpoints` (server-generated HMAC signing secret)
- [x] Event emission — `POST /events` (FK to endpoints, stored as `pending` before delivery)
- [x] Synchronous delivery — `deliver_event` (httpx POST + 5s timeout), `delivery_attempts` log (2nd FK), status → `delivered`/`failed`, all 3 outcomes verified
- [x] Async delivery via Redis queue + worker — delivery moved off the request path (producer enqueues event id, worker drains queue and delivers)
- [x] Read paths — `GET /events`, `GET /events/{id}` + CORS for the dashboard
- [x] Retries, exponential backoff, dead-letter queue, idempotency *(✅ retry schema + crash-hardened worker, ✅ retry-on-failure w/ cap, ✅ reliable queue (`BLMOVE`) + crash recovery, ✅ exponential backoff + jitter (ZSET scheduled queue), ✅ dead-letter queue + `POST /dlq/replay`, ✅ idempotency — terminal-status guard + `X-Idempotency-Key` header)*
- [x] Frontend dashboard (Next.js) *(✅ emit-and-watch page, ✅ events list + status badges, ✅ DLQ view + replay button)*
- [x] HMAC request signing — every delivery signed over the exact bytes sent (`X-Webhook-Signature` + `X-Webhook-Timestamp`), replay-protected, verified end-to-end against an independent receiver
- [ ] API-key auth + rate limiting
- [ ] Retry policy by status class (retry `5xx`/timeouts, dead-letter most `4xx` immediately)
- [ ] Secret encryption at rest + secret rotation
- [ ] CI/CD, Terraform, cloud deploy, monitoring

---

*A learning + portfolio project focused on production backend patterns and DevOps fundamentals.*

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
- Payloads are **signed (HMAC)** so receivers can verify authenticity
- **Every delivery attempt is logged** for full observability

## Tech Stack

| Layer | Tech |
|-------|------|
| API | FastAPI + Pydantic |
| Persistence | PostgreSQL + SQLAlchemy + Alembic |
| Async / queue | Redis + workers |
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

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check → `{"status": "ok"}` |
| `POST` | `/endpoints` | Register a webhook endpoint |
| `GET` | `/endpoints` | List endpoints (paginated: `?skip=&limit=`) |
| `GET` | `/endpoints/{id}` | Get one endpoint (404 if missing) |
| `POST` | `/events` | Emit an event for an endpoint |

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

A background **worker** (`python -m app.worker`) blocks on the queue, loads each event from the DB,
POSTs the payload to the endpoint's URL (`httpx`, 5s timeout), records the outcome in
`delivery_attempts`, and updates the event's `status` to `delivered` or `failed`.

Returns `201` — meaning the event was *accepted and stored*, not that it was delivered (a failed
delivery is retried later; see roadmap). If the referenced `endpoint_id` doesn't exist, returns `404`
(validated in the app layer; the DB foreign key is a safety net).

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
| Receiver returns non-`2xx` | `false` | e.g. `500` | `failed` |
| Receiver unreachable / timeout | `false` | `NULL` (no response) | `failed` |

## Roadmap

- [x] Layered FastAPI scaffold + `/health` endpoint
- [x] Dockerize app + Postgres + Redis via docker-compose
- [x] Data layer — SQLAlchemy engine/session, `Endpoint` model, Alembic migrations
- [x] Endpoint registration — `POST /endpoints` (server-generated HMAC signing secret)
- [x] Event emission — `POST /events` (FK to endpoints, stored as `pending` before delivery)
- [x] Synchronous delivery — `deliver_event` (httpx POST + 5s timeout), `delivery_attempts` log (2nd FK), status → `delivered`/`failed`, all 3 outcomes verified
- [x] Async delivery via Redis queue + worker — delivery moved off the request path (producer enqueues event id, worker drains queue and delivers)
- [ ] Retries, exponential backoff, dead-letter queue, idempotency
- [ ] HMAC signatures + API-key auth + rate limiting
- [ ] CI/CD, Terraform, cloud deploy, monitoring

---

*A learning + portfolio project focused on production backend patterns and DevOps fundamentals.*

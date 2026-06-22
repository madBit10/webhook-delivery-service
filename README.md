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

# 2. create a .env with: DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
#    then start Postgres
docker compose up -d

# 3. apply database migrations
alembic upgrade head

# 4. run the app
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger): http://127.0.0.1:8000/docs

## Roadmap

- [x] Layered FastAPI scaffold + `/health` endpoint
- [x] Dockerize app + Postgres via docker-compose *(Redis later)*
- [x] Data layer — SQLAlchemy engine/session, `Endpoint` model, Alembic migrations
- [ ] Endpoint registration + event emission (Postgres-backed)
- [ ] Async delivery via Redis workers
- [ ] Retries, exponential backoff, dead-letter queue, idempotency
- [ ] HMAC signatures + API-key auth + rate limiting
- [ ] CI/CD, Terraform, cloud deploy, monitoring

---

*A learning + portfolio project focused on production backend patterns and DevOps fundamentals.*

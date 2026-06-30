# 📘 Day 3 — Endpoint Registration (`POST /endpoints`)

> **What I built today:** the first *real* feature — registering a webhook endpoint. A client
> POSTs a `url` + `event_types`, the server generates a secret signing key, stores the row in
> Postgres, and returns the created endpoint **without leaking the secret**. This was the first
> time all four layers (schema → service → repository → router) connected end to end and a
> request flowed all the way to the database and back.

---

## 🧱 1. The Full Vertical Slice (the layers finally connect)

Day 1 built the *empty* layers; Day 2 built the *data* layer. Today I wired a single feature
**straight down through all of them** — a "vertical slice."

```
Client (Swagger)
   │  POST /endpoints  { url, event_types }
   ▼
Router      app/api/routes/endpoint.py     ← HTTP: parse body, inject db, return
   │  register_endpoint(db, data)
   ▼
Service     app/services/endpoint.py       ← business logic: GENERATE the secret
   │  create_endpoint(db, url, event_types, secret)
   ▼
Repository  app/db/repository.py           ← the only layer that touches SQLAlchemy
   │  add → commit → refresh
   ▼
Database    Postgres  →  endpoints table
```

**The data changes shape as it flows:**
> `EndpointCreate` (in) → `Endpoint` (stored row, has secret + id) → `EndpointRead` (out, no secret)

Three different shapes for three different jobs. That's the whole mental model of this feature.

---

## 🧬 2. Three Schemas, Three Jobs (input vs storage vs output)

A single concept has **multiple shapes** depending on direction:

| Shape | File | What it is | Has `secret`? | Has `id`? |
|-------|------|------------|:---:|:---:|
| `EndpointCreate` | `schemas/endpoint.py` | **INPUT** — what the client sends | ❌ | ❌ |
| `Endpoint` | `db/model.py` | **STORAGE** — the SQLAlchemy ORM row | ✅ | ✅ |
| `EndpointRead` | `schemas/endpoint.py` | **OUTPUT** — what we send back | ❌ | ✅ |

**Why three and not one?**
- `EndpointCreate` has **no `secret`** because the client must NOT choose it — the *server* generates it. (You can only ask for what a client is allowed to provide.)
- `EndpointRead` has **no `secret`** because you never echo the signing key back in a normal read.
- `Endpoint` (the ORM model) has **everything**, because the database is the full source of truth.

**🎤 Interview Q:** *Why separate input schema, output schema, and DB model?*
→ Security + decoupling. The client can't set server-controlled fields (secret), the API never
leaks sensitive columns, and the DB schema can evolve without changing the public contract.

**`from_attributes=True` (on `EndpointRead`):** lets Pydantic build the output model straight
from a SQLAlchemy object by *reading its attributes* (`endpoint.id`, `endpoint.url`, …) instead
of needing a dict. This is what makes `return endpoint` "just work" through `response_model`.

---

## 🧠 3. Instance vs Class — `data.url`, NOT `Endpoint.url`

A trap I hit: I almost wrote `url = Endpoint.url`. Wrong.

- **`data`** = the *instance* of `EndpointCreate` passed in → holds **real values** (`data.url`
  is `"https://..."`). This is the input.
- **`Endpoint`** = the *class* (the table blueprint). `Endpoint.url` is a **`Column` object**, a
  schema descriptor — **not a value**. You only get real values from an *instance* of `Endpoint`.

> 🧠 **Rule:** read values from the **input instance** (`data.url`); store them into the **table
> class** (`Endpoint(url=...)`). Never read data off a class.

---

## ⚙️ 4. The Service Layer — Business Logic Lives Here

```python
# app/services/endpoint.py
import secrets

def register_endpoint(db: Session, data: EndpointCreate) -> Endpoint:
    secret = secrets.token_hex(32)             # ← the business rule
    endpoint = create_endpoint(
        db=db, url=data.url, event_types=data.event_types, secret=secret,
    )
    return endpoint
```

**Why a service at all?** The repo is "dumb" — it *requires* a secret but doesn't make one.
`EndpointCreate` *has* no secret. Something in the middle must **decide** to generate it. That
decision is *business logic*, and business logic belongs in the **service**, not the router (HTTP
plumbing) and not the repo (DB plumbing).

**Design decision I made:** the service takes the **whole `EndpointCreate` object**, not unpacked
primitives. Trade-off — passing the object means adding a field later doesn't change the service
signature; the (small) cost is the service importing an API-layer schema. Practical choice for
this project; I can defend either side.

### 🔐 `secrets` vs `random` (the security point)

The secret will later be used to **HMAC-sign** outgoing webhooks so the *receiver* can verify
"this really came from us, untampered" — exactly how Stripe/GitHub webhooks work. So it must be
**unguessable**.

- `random` → *pseudo*-random, predictably seeded. Great for shuffling, **never** for security
  tokens (an attacker who sees enough output can predict the rest).
- `secrets` → built on the OS cryptographically-secure RNG (`/dev/urandom`). Unpredictable.
- `secrets.token_hex(32)` → 32 random bytes as a **64-char hex string**.

**🎤 Interview Q:** *Why `secrets` over `random` for tokens?* → `random` is deterministic/
predictable; `secrets` is cryptographically secure. Predictable signing keys = forgeable webhooks.

---

## 🗄️ 5. The Repository — Kept Dumb on Purpose

```python
def create_endpoint(db: Session, url, event_types, secret) -> Endpoint:
    endpoint = Endpoint(url=url, event_types=event_types, secret=secret)
    db.add(endpoint)       # stage in the session (not in DB yet)
    db.commit()            # flush + make permanent on disk
    db.refresh(endpoint)   # re-read the row → get DB-generated values (the id)
    return endpoint
```

- Only this layer imports SQLAlchemy → service + router stay DB-agnostic and testable.
- It **receives** a secret and stores it; it makes **no decisions**. That's deliberate.
- `db.refresh()` is *why* the response comes back with `id: 1` — the auto-increment PK is
  generated by Postgres, so we re-read the row to learn it.
- ⏭️ **Deferred:** error handling / `rollback` on failure. Repo has none yet.

---

## 🔌 6. The Router — Dependency Injection + `response_model`

```python
@router.post("/endpoints", response_model=EndpointRead, status_code=201)
def create_endpoint_route(data: EndpointCreate, db: Session = Depends(get_db)):
    endpoint = register_endpoint(db, data)
    return endpoint          # just the object — FastAPI does the rest
```

### `Depends(get_db)` — Dependency Injection
The route *declares* "I need a DB session"; FastAPI **supplies** it (calls `get_db`, hands over
what it `yield`s) and **cleans up** (runs the `finally: db.close()`) after the request. I never
fetch or close the session myself → I can't leak connections, and the route can be tested with a
fake DB. (This is what consumes the `yield`-based `get_db` I wrote on Day 2.)

> 🫏 **Donkey law:** `Depends` = "I declare what I need; the stable-master brings the work-table
> and folds it away. I just stamp the note."

### `response_model=EndpointRead` — the leak-proof filter
I `return` a full `Endpoint` (which **has** a secret), but FastAPI pours it through
`EndpointRead` before sending → only `EndpointRead`'s fields survive → **the secret never goes
over the wire.** *What I store ≠ what I expose.*

### `status_code=201`
Correct HTTP status for "created a new resource" (default would be 200). The status code rides on
the *envelope*, not in the JSON body.

### 🟨 FastAPI (declarative) vs Express (imperative)
My JS reflex was `res.status(201).json(endpoint)` — driving the response by hand. FastAPI flips it:

| Job | Express (JS) | FastAPI |
|-----|--------------|---------|
| Set status | `res.status(201)` | `status_code=201` on decorator |
| Send body | `res.json(obj)` | `return obj` |
| Filter output | manual | `response_model=...` |
| Read body | `req.body` | `data: EndpointCreate` param |

> 🫏 Express: *you* drive the truck. FastAPI: *you write the address on the box* and the company drives it.

---

## 🌐 7. REST Naming — Plural Collections

Route is `POST /endpoints` (plural), not `/endpoint`. `/endpoints` is the **collection**; POSTing
to it adds one. This keeps the future siblings consistent:
- `GET /endpoints` → list all
- `GET /endpoints/{id}` → get one
- `POST /endpoints` → create one

---

## 🐛 8. The Config Bug — `pydantic-settings` `extra_forbidden`

First full app boot crashed with 3 validation errors (`postgres_user/password/db` →
`extra_forbidden`). **Cause:** `Settings` declares only `database_url`, but `.env` *also* holds the
`POSTGRES_*` trio (added Day 2 so docker-compose can use `${...}`). Pydantic v2 `BaseSettings`
reads the **whole** `.env` and **forbids undeclared keys by default**.

**Fix:**
```python
class Config:
    env_file = ".env"
    extra = "ignore"     # skip env keys the app doesn't declare
```

**Why `ignore` (not declare-all, not `allow`):** those vars are for **compose**, not the Python
app — the app genuinely only needs `database_url`. `ignore` keeps `Settings` honest (only what the
app uses) and avoids exposing secrets as `settings` attributes.

> 🫏 The settings form had **one** blank but the `.env` envelope held **four** slips. Strict
> Pydantic rejected the extras until I told it "ignore slips you don't have a slot for."

**🎤 Lesson:** an app's `Settings` should model the **app's** needs; infra-only env vars can be
ignored rather than forced into the schema.

---

## ✅ 9. Verifying End to End

1. `docker compose up -d` → Postgres up.
2. `uvicorn app.main:app --reload` → API up (`Application startup complete.`).
3. Swagger at `http://127.0.0.1:8000/docs` → **Try it out** on `POST /endpoints`.
4. Response: **201** + `id: 1` + `is_active: true` + **no `secret`** ✅
5. **Proof the secret really persisted** (response hides it — that's API filtering, not storage):
   ```bash
   docker compose exec db psql -U example -d exampledb \
     -c "SELECT id, url, event_types, is_active, secret FROM endpoints;"
   ```
   → row shows the 64-char hex secret. **Stored in the DB, never sent to the client.** 🛡️

---

## 🫏 10. Donkey Translation (this feature)

| 🫏 Donkey | 🛠️ Technical term |
|-----------|-------------------|
| Customer fills out a "deliver my carrots here" form | `POST /endpoints` with `EndpointCreate` |
| Form only asks for address + which news they want | input schema (no secret field) |
| We secretly assign them a tamper-proof wax seal | server-generated `secrets.token_hex(32)` |
| Chef decides "every customer gets a seal" | service layer (business logic) |
| Storeroom just files the card | repository (dumb DB write) |
| Receipt handed back — seal blacked out | `response_model=EndpointRead` filtering |
| Stable-master lends a work-table per note | `Depends(get_db)` |
| "Created!" stamp on the envelope | `status_code=201` |

---

## 🎤 11. Interview Pitch (honest upgrade)

> Previously "I'm building…". Now the registration path is **built**: *"Clients register a webhook
> endpoint via a REST API; the service generates a cryptographically-secure signing secret on the
> server, persists it in Postgres, and the API is designed so the secret is never exposed in any
> response — separate input/output schemas enforce that. It's a clean layered design: HTTP router,
> a service holding business logic, and a repository that's the only layer touching the ORM."*

---

## ✅ Status & next

**Day 3 complete — endpoint registration built, tested, and merged to `main`.**
1. ✅ Schemas (input/output split, no secret leak)
2. ✅ Repository `create_endpoint`
3. ✅ Service `register_endpoint` (secret via `secrets`)
4. ✅ Router `POST /endpoints` (DI, `response_model`, 201) + wired into `main.py`
5. ✅ Fixed `pydantic-settings` `extra="ignore"` config bug
6. ✅ Verified via Swagger + `psql`

> Branch `feat/endpoint-registration` → committed, pushed, PR'd, merged to `main`. ✅

**Next session ideas:**
- **List/read endpoints** — `GET /endpoints` and `GET /endpoints/{id}` (pagination later).
- **Repository error handling** — `rollback` on failure (the deferred piece).
- **Input validation** — make `url` a proper `HttpUrl`, validate `event_types`.
- Then the heart of the project: **events + delivery attempts** (retries, backoff, DLQ).

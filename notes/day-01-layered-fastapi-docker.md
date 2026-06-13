# 📘 Day 1 — Layered FastAPI App + Docker

> **What I built today:** a layered FastAPI app with a `/health` endpoint, running inside a Docker container — plus clean git hygiene (README baseline on `main`, feature branches, PRs).

---

## 🧱 1. Layered Architecture (Separation of Concerns)

**In plain words:** A bad backend is one giant file where everything is tangled together — like a restaurant where the waiter also cooks, cleans, and does the accounting. A good backend splits work into **layers**, each with **one job**, each talking only to the layer below it.

```
HTTP request
   │
   ▼
Router        ← "the waiter"  : receives request, validates, returns response (knows HTTP, not DB)
   │
   ▼
Service       ← "the chef"    : the business logic / rules (knows nothing about HTTP or SQL)
   │
   ▼
Repository    ← "the pantry"  : talks to the database (knows nothing about business rules)
   │
   ▼
Database
```

**Why it matters:**
- ✅ **Testable** — you can test the service with a *fake* repository, no real DB needed.
- ✅ **Swappable** — change Postgres → MySQL by rewriting only the repository.
- ✅ **Clean contracts** — Pydantic schemas define data shape, decoupled from DB models.

**🎤 Interview Q:** *Why separate your API schema from your DB model?*
→ So your database structure isn't leaked to clients, and the two can evolve independently.

**Rule of thumb (YAGNI):** Only create a layer the day it has a real job. Day 1 had no DB or logic, so we built *only* routers — no `services/`, `repositories/`, or `models/` yet.

---

## 🔌 2. FastAPI Routers (`APIRouter` + `include_router`)

**The problem:** Writing `@app.get(...)` directly on `app` puts *every* route in one file. With dozens of routes, you're back to the giant tangled file.

**The fix:** Define routes on a **router** in their own module, then **plug it into** the app.

```python
# app/api/routes/health.py
from fastapi import APIRouter

router = APIRouter()

@router.get("/health")
def health_check():
    return {"status": "ok"}
```

```python
# app/main.py
from fastapi import FastAPI
from app.api.routes import health      # import the MODULE

app = FastAPI()
app.include_router(health.router)       # mount its routes onto the app
```

**⚠️ Import gotcha I hit:**
- ✅ `from app.api.routes import health` → `health` is the **module** → use `health.router`
- ❌ `from app.api.routes.health import health` → **fails**, there's no `health` *object* inside (only `router` and `health_check`)
- The module-import style scales better — no name clash when many files each name their router `router`.

**`__init__.py`:** an (empty) file that marks a folder as a Python **package**, so imports like `from app.api.routes import health` work.

---

## 🐳 3. Docker

**In plain words:** A **container** is a sealed box holding your app + its exact dependencies + a pinned Python version, so it runs *identically* on your laptop, in CI, and in the cloud. Kills "works on my machine."

| Term | Meaning |
|------|---------|
| **Image** | The blueprint (built once from a `Dockerfile`) |
| **Container** | A running instance of an image (you can run many) |

### The Dockerfile, line by line

```dockerfile
FROM python:3.12-slim          # pinned modern base image
WORKDIR /code                  # the "current dir" inside the container
COPY requirements.txt .        # copy deps FIRST...
RUN pip install --no-cache-dir -r requirements.txt   # ...and install them
COPY ./app ./app               # THEN copy the code
EXPOSE 8000                    # document the port
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**🧠 Memory hook:** *`WORKDIR` sets the room; `COPY` moves the furniture in; relative paths are measured from that room.*

### The two interview-gold gotchas

**① Layer caching — deps before code.**
Docker caches each instruction as a layer. Dependencies change *rarely*, code changes *constantly*. By installing deps **before** copying code, editing your code won't re-trigger a full reinstall every build. Flip the order → every tiny code edit reinstalls everything (slow).

**② Bind to `0.0.0.0`, not `127.0.0.1`.**
Inside a container, `127.0.0.1` means "only reachable from *inside* this container" — the host can't reach it. `0.0.0.0` = "listen on all interfaces" so the mapped port works.

### `EXPOSE` vs `-p` — a key distinction
- `EXPOSE 8000` in the Dockerfile only **documents** the port.
- `-p 8000:8000` on `docker run` actually **publishes** it (`host:container`). **This is the line that opens the door.**

### Build & run
```bash
docker build -t webhook-delivery-service .   # -t = name/tag, . = build context
docker run -p 8000:8000 webhook-delivery-service
```

**`.dockerignore`** (like `.gitignore`, for the build context): exclude `venv/`, `__pycache__/`, `.git/`, `.env`, `tests/` → smaller, cleaner image.

---

## 🌿 4. Git Hygiene

- **`.gitignore` principle:** never commit (a) **generated files** (`__pycache__/`, `*.pyc`, `venv/`) or (b) **secrets** (`.env`). A leaked `.env` on public GitHub is a real incident — bots scrape for credentials within minutes.
- **`pip freeze > requirements.txt`** dumps the *full* pinned dependency tree (including transitive deps) → reproducible environment. (`pip freeze` takes no package name.)
- A branch ref **doesn't exist until its first commit** — `git branch` shows nothing on an unborn branch, and switching away from one deletes it.
- **Rename a branch:** `git branch -m new` (current) or `git branch -m old new` (other).
- **`git branch --contains <hash>`** tells you which branches contain a commit — great for confirming a merge landed.

---

## 🫏 5. The Donkey Translation (recall aid)

**Story:** Farm A grows carrots (*Producer*). Farm B wants to know the instant one is ready (*Subscriber*). We're the **donkey delivery company** in the middle (*the webhook service*) — we carry "carrot ready!" messages reliably, even when Farm B's door is locked.

| 🫏 Donkey | 🛠️ Technical term |
|-----------|-------------------|
| Carrot farm (sends news) | Producer / Publisher |
| Farm wanting the news | Subscriber / Consumer |
| The donkey company (us) | Webhook delivery service |
| Farm B's door/address | Endpoint (subscriber URL) |
| "Carrot is ready!" message | Event |
| "I'll bug you when there's news" note | Webhook |
| Asking "ready yet?" every 5 min | Polling (wasteful) |
| Donkey hands over the note | HTTP POST |
| "We're open and healthy!" bell | `/health` endpoint |
| Door locked? Try again | Retries |
| Wait 1 min, then 2, then 4… | Exponential backoff |
| …plus a few random seconds | Jitter (avoid stampede) |
| The "couldn't deliver" basket | Dead-letter queue (DLQ) |
| Don't deliver the same carrot twice | Idempotency |
| "The message WILL get there" | At-least-once delivery |
| Secret stamp on the note | HMAC signature |
| The trip logbook | Delivery attempt logging |
| Donkeys working in the back | Async workers + queue |
| Note's journey status | State machine |
| Front desk / chef / storeroom | Layered architecture |
| A tray of services | `APIRouter` |
| The magic shipping box | Docker container |
| The company's filing cabinet | Database (Postgres) |
| Membership card | API-key auth |
| "Only X donkeys/hour" | Rate limiting |

---

## 🎤 6. Interview Pitch

> **⚠️ Honesty rule:** only claim what's actually *built* at interview time. Today that's the scaffold + `/health` + Docker, so the honest framing is *"I'm building…"* and you describe the design. Upgrade "designing" → "built" as each week completes.

**30-second version:**
> *"I built a reliable webhook delivery service — the infrastructure layer that guarantees an event eventually reaches its subscriber, even when the receiver is down. Same kind of system that powers Stripe's and GitHub's webhooks. The interesting part isn't sending an HTTP request — it's making delivery reliable: retries with exponential backoff, a dead-letter queue, idempotency so retries don't double-process, and HMAC signatures for authenticity. Built with FastAPI, Postgres, and Redis-backed async workers, fully containerized with Docker and CI/CD."*

**Why it works:** leads with the *problem* not the tech · names the hard parts (backoff, DLQ, idempotency, at-least-once) · uses a relatable anchor (Stripe/GitHub).

---

✅ **Day 1 complete.** Next: the data layer — Postgres + SQLAlchemy + docker-compose + the first `endpoints` table.

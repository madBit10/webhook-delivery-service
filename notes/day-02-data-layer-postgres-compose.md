# 📘 Day 2 — The Data Layer (Postgres + docker-compose)

> **What I did today:** gave the app a durable database. Learned *why* a DB + *why* an ORM,
> then wrote a `docker-compose.yml` to run Postgres alongside the app and booted it
> successfully. **Paused after getting Postgres live** — SQLAlchemy wiring is next.

---

## 💾 1. Why a Database + Why an ORM

**In plain words:** On Day 1 the app had no memory — restart it and all data is gone (like
notes on your hand). A **database** is a durable filing cabinet: data survives restarts,
crashes, redeploys. For a webhook service this is non-negotiable — an event we promised to
deliver *cannot* vanish because a process restarted.

- **Postgres** = the relational database we're using (tables, rows, columns, relationships,
  ACID transactions). Runs as its own program, separate from the app.
- **The friction:** Postgres speaks **SQL**; the app speaks **Python objects**.
- **ORM (Object-Relational Mapper)** = the translator between them. **SQLAlchemy** is ours.

**The mapping (one-sentence definition):**
> An ORM maps a Python **class ↔ table**, an **instance ↔ row**, an **attribute ↔ column**.
> You manipulate objects; the ORM emits the SQL.

**🎤 Why use an ORM (interview answer):**
1. Write Python instead of hand-written SQL strings → fewer bugs, more readable.
2. **SQL-injection protection** — the ORM parameterizes queries automatically.
3. **Database-agnostic** — swap Postgres for another DB with minimal change.
4. Manages **connections, sessions, transactions** for you.

**The trade-off (senior-level point):** an ORM adds abstraction — can produce inefficient
queries (the **N+1 query problem**), and you sometimes drop to raw SQL for complex/hot paths.
A senior knows the ORM *and* its escape hatches.

**Ties to Day 1 layers:** the **Repository** is the *only* layer allowed to import/use
SQLAlchemy. Service + Router stay database-agnostic → testable + swappable. (That's *why*
we built the layers first.)

---

## 🐳 2. docker-compose — running Postgres alongside the app

**The problem:** Day 1 ran *one* container by hand (`docker build` + `docker run`). Now the
system has **two** programs that must run together (app + Postgres). Wiring that by hand is
painful.

**The fix:** `docker-compose.yml` *declares* all the pieces, brought up with one command
(`docker compose up`). Compose also puts services on a **shared private network** so they
find each other **by service name** (e.g. the app reaches Postgres at host `db`, not an IP).

### The file I wrote

```yaml
services:
  db:
    image: postgres:16                    # pinned version, NOT :latest
    environment:                          # Postgres reads these on FIRST boot to init itself
      POSTGRES_USER: example
      POSTGRES_PASSWORD: password
      POSTGRES_DB: exampledb
    ports:
      - "5432:5432"                       # "HOST:CONTAINER" — list item (dash), quoted
    volumes:
      - db-data:/var/lib/postgresql/data  # ① MOUNT (list/dash): mounts volume into container

volumes:                                  # ② DECLARE (top-level, mapping/no-dash)
  db-data:
```

### ⚠️ The two-`volumes:` gotcha (the thing I fought through today)

A complete file has **TWO `volumes:` keys** doing different jobs:
- **① inside `db`** → a **list** (dash, `name:/path`, NO space): *"mount volume `db-data`
  at this path in the container."*
- **② top-level** (sibling of `services:`) → a **mapping** (`db-data:`, no dash, no path):
  *"declare this named volume exists so Compose creates/manages it."*

Both required. ① *uses* it, ② *declares* it, same name connects them.
- `db-data: /path` (colon + space) = mapping = ❌ wrong inside a service
- `- db-data:/path` (dash, no space) = list item = ✅ right

**Why the volume matters at all:** without it, `docker compose down` wipes every table.
The named volume persists data *outside* the container — that's what makes the DB durable.

### YAML rule that caused every mistake
- **List** → `- item` (dash per entry). `ports` and a service's `volumes` are lists.
- **Mapping** → `key: value` (colon + space). `environment` and the top-level `volumes` are mappings.

### Postgres first-boot log (don't panic)
Postgres does a **two-phase first boot**: phase 1 runs `initdb` (creates the cluster +
`exampledb` + `example` user → you see `CREATE DATABASE` then `server stopped`), phase 2
boots the **real** server. The mid-log "shutting down" is **normal, not an error**.
Success line:
```
LOG:  database system is ready to accept connections   (listening on 0.0.0.0, port 5432)
```
`0.0.0.0` again — same lesson as Day 1's `CMD`: listen on all interfaces so the host mapping works.
On future boots the init phase is skipped because data persists in the `db-data` volume.

### Useful commands
```bash
docker compose up            # start (foreground, streams logs)
docker compose up -d         # start detached (frees the terminal) ← use for dev
docker compose logs -f db    # tail a service's logs
docker compose ps            # what's running
docker compose down          # stop + remove containers (named volume SURVIVES)
```

---

## 3. SQLAlchemy engine + session (connect Python → Postgres)

Three core objects in `app/db/database.py`:
- **`engine = create_engine(DATABASE_URL)`** — a managed **pool** of connections to Postgres
  (opening a connection is expensive, so we keep a few ready and reuse them). One per app.
- **`SessionLocal = sessionmaker(bind=engine)`** — a *factory* that hands out **Session** objects.
  A Session is the **workspace for one unit of work**: add/query objects, then `commit()` or `rollback()`.
  Short-lived — one per request.
- **`Base = declarative_base()`** — the parent class every model inherits from; it holds the
  `metadata` (the registry of all tables) that Alembic later reads.
- **`get_db()`** — FastAPI dependency using `yield` + `finally: db.close()` so every borrowed
  connection is **always** returned to the pool, even on error.

Proved end-to-end with `test_db.py` (`SELECT 1` → printed `1`).

## 4. Config via pydantic-settings + `.env` (no secrets in code)

- `app/core/config.py`: `Settings(BaseSettings)` reads `DATABASE_URL` from `.env`.
- Moved `POSTGRES_USER/PASSWORD/DB` **out of** `docker-compose.yml` into `.env`; the compose file now
  only references them via `${POSTGRES_USER}` etc. → **committed files contain zero secrets**
  (`.env` is gitignored). Verify substitution with `docker compose config`.
- ⚠️ **YAML indent gotcha (again):** all env vars must sit one level *under* `environment:` at the
  same depth — a mis-indented line becomes a service property and Compose errors.
- ⚠️ `POSTGRES_*` only take effect on **first boot** (they init the data volume). Changing them later
  needs `docker compose down -v` to recreate the volume.

## 5. First model + Alembic migrations (went the production route)

- `app/db/model.py`: `Endpoint(Base)` → table `endpoints` with `id` (PK, indexed, auto-increment via a
  Postgres **sequence**), `url`, `secret`, `event_types` (CSV for now), `is_active`.
  - **`nullable=False` = SQL `NOT NULL`** (the correct equivalent of "required"; `required=` is Pydantic, not SQLAlchemy).
  - **App-side `default=True` vs DB-side `server_default`:** `default=True` is filled in by the ORM, not the
    database — so it does **not** appear in the migration / `\d endpoints`. Fine for ORM inserts; use
    `server_default` if raw SQL inserts must get the default too.
- **Alembic = "Git for the database schema."** Migration = commit, revision id = hash,
  `down_revision` = parent, `alembic_version` table = the DB's "which migration am I on" sticker,
  `upgrade()`/`downgrade()` = apply/undo, `--autogenerate` = diff models vs. real DB into a script.
- Setup: `pip install alembic` → `alembic init alembic` → wired `env.py`
  (`target_metadata = Base.metadata`, **import the model module so the table registers**, pull the URL
  from `settings`) → `alembic revision --autogenerate -m "create endpoints table"` → **reviewed the
  generated file** → `alembic upgrade head`.
- Verified: `\d endpoints` shows all 5 columns NOT NULL; `alembic_version` = `576715d853ee`.

---

## ✅ Status & next

**Day 2 complete (all 5 steps) — chose the production path (Alembic) over `create_all`:**
1. ✅ Why a database + why an ORM
2. ✅ `docker-compose` → Postgres running & verified
3. ✅ SQLAlchemy engine + session (proven via `test_db.py`)
4. ✅ Config via pydantic-settings + `.env`; secrets moved out of compose
5. ✅ First model `endpoints` — created via **Alembic migration**

> Branch `feat/data-layer` → committed, pushed, merged to `main`. ✅

**Next session:** endpoint **registration** — API route + **repository** layer to insert rows into
`endpoints`. New concepts to learn: CRUD, commit/rollback in practice, repository pattern,
**Pydantic schema vs. ORM model**.

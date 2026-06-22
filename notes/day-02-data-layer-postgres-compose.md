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

## ✅ Status & next

**Done today (steps 1–2 of 5):**
1. ✅ Why a database + why an ORM
2. ✅ `docker-compose` → Postgres running & verified

**Next session (steps 3–5):**
3. ⏭️ **SQLAlchemy engine + session** — connect Python to this Postgres
4. Config via **pydantic-settings + `.env`** — DB connection string, no secrets in code
   (also move the `POSTGRES_*` values out of `docker-compose.yml` into `.env`)
5. **First model** — the `endpoints` table (url, secret, event_types, is_active)

> Branch for this work: `feat/data-layer` (off `main`).

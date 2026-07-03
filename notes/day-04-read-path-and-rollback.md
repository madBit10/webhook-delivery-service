# 📘 Day 4 — Read Path (`GET /endpoints`) + Transaction Rollback

> **What I built:** the *read* side of the endpoints resource — list-with-pagination and
> get-one-by-id (with a real 404) — plus input URL validation. Then a hardening pass: wrapping the
> write in a transaction `try/except` so a failed insert rolls back cleanly instead of poisoning
> the session. First time I wrote **queries** (not just inserts) and handled **transactions** by hand.

---

## 🗄️ 1. Querying — Reading Is Different From Inserting

Day 3 pushed data *down* (`add → commit → refresh`). Reads pull data *up*, via `db.query(...)`:

```python
db.query(Endpoint)                                  # a pending SELECT — nothing runs yet
db.query(Endpoint).all()                            # → runs it → LIST of Endpoint objects
db.query(Endpoint).filter(Endpoint.id == id).first()# → runs it → ONE object or None
```

**Two repo functions I added (`app/db/repository.py`):**
```python
def get_endpoints(db, skip=0, limit=100) -> list[Endpoint]:
    return db.query(Endpoint).offset(skip).limit(limit).all()

def get_endpoint(db, endpoint_id) -> Optional[Endpoint]:
    return db.query(Endpoint).filter(Endpoint.id == endpoint_id).first()
```

**Key differences from the write:**
- **`.all()` vs `.first()`** — `.all()` → a list (empty `[]` if none); `.first()` → one object **or `None`**. That `None` is what drives the 404 later.
- **Reads don't `commit`.** Nothing is changing, so there's nothing to make permanent. *Writes commit, reads don't.*

**`Endpoint.id == id` inside `.filter()` is correct class usage** — this seems to contradict Day 3's
"don't read values off the class," but it doesn't: here I'm *not* reading a value, I'm referencing the
**column** to build a `WHERE` clause. SQLAlchemy overloads `==` on columns to emit SQL, not to compare
in Python. Same `Endpoint.id`, totally different job (build query vs read data).

---

## 📄 2. Pagination — Never Return Everything

`.all()` on a 3-million-row table melts the DB and the response. So reads are paginated:

```python
db.query(Endpoint).offset(skip).limit(limit).all()   # SQL: OFFSET :skip LIMIT :limit
```
- `skip` (OFFSET) = how many rows to jump past
- `limit` (LIMIT) = max rows to return

**🐛 Bug I hit:** the *service* accepted `skip`/`limit` but called `get_endpoints_repo(db)` — dropping
them. It didn't crash; it just **silently ignored** the pagination. Lesson: if the signature *promises*
a param, the body has to *deliver* it. (Test with `limit=0` → empty list proves it's wired.)

---

## 🧭 3. Path Params vs Query Params

```python
@router.get("/endpoints", response_model=list[EndpointRead])
def list_endpoints_route(skip: int = 0, limit: int = 100, db=Depends(get_db)):
    ...

@router.get("/endpoints/{endpoint_id}", response_model=EndpointRead)
def get_endpoint_route(endpoint_id: int, db=Depends(get_db)):
    ...
```

- **Path param** — `{endpoint_id}` in the URL. The function arg name **matches** the `{...}`, so FastAPI
  pulls it from the path and coerces to `int` (a non-int like `/endpoints/abc` → auto 422).
- **Query param** — `skip`/`limit` are *not* in the path → FastAPI reads them from the query string:
  `GET /endpoints?skip=0&limit=50`.
- **Rule of thumb:** *identity* in the path (`/endpoints/5` = which one), *options* in the query
  (`?skip&limit` = how to shape the response).
- **`response_model=list[EndpointRead]`** — returning a list → filter each item through `EndpointRead`
  (secret stripped from **every** element).

**⚠️ Route ordering:** FastAPI matches top-to-bottom. Static paths before dynamic ones, or `/endpoints/active`
could get swallowed by `/endpoints/{endpoint_id}` (treating `"active"` as an id).

---

## 🚫 4. The 404 — Turning `None` Into an HTTP Error

```python
from fastapi import HTTPException

endpoint = get_endpoint(db, endpoint_id)
if endpoint is None:
    raise HTTPException(status_code=404, detail="Endpoint not found")
return endpoint
```

The repo returns `None` when nothing matches. **Returning `None` directly would send `200 OK` with a
`null` body — a lie** ("success, here's your endpoint" when there isn't one). `raise HTTPException(404)`
makes FastAPI send a real `404 Not Found` + `{"detail": "..."}`.

> 🫏 Someone asks for the delivery card of customer #99 who doesn't exist — you don't hand them a blank
> card and smile (`200 + null`), you say "no such customer" (`404`).

---

## 🧪 5. Input-Strict, Output-Lenient (the 500 I debugged)

Made `url` a real URL with Pydantic's `HttpUrl`. **But I put it on the wrong schema** and got a **500 on GET**:

- `EndpointCreate.url` was still `str` → junk like `"banana"` was **accepted** and stored.
- `EndpointRead.url` was `HttpUrl` → **`response_model` validates OUTPUT too**, so reading the `"banana"`
  row back failed validation → `ResponseValidationError` → **500** (server produced bad data).

**The rule I learned:**
> **`response_model` validates in BOTH directions.** Put strict types on the **input** schema
> (`EndpointCreate` — reject junk at the door); keep the **output** schema lenient (`EndpointRead.url: str`
> — just reflect what's stored). Don't re-police already-saved data on the way out.

**Fix:**
```python
class EndpointCreate(BaseModel):
    url: HttpUrl      # validate INPUT strictly (junk → 422)
class EndpointRead(BaseModel):
    url: str          # OUTPUT reflects the stored string
```
**Gotcha:** in Pydantic v2 `HttpUrl` is a `Url` object, not a `str`. So the service converts it for storage:
`url=str(data.url)`. Validation guards the front door; `str(...)` packs it for the DB.

**🎤 Interview Q:** *Where do you validate?* → As early as possible, on **input**. Never trust output
validation to catch bad data — by then it's already in your database.

---

## 🧱 6. The Empty Pass-Through Service (why keep it?)

The read services are pure pass-throughs (`return get_endpoints_repo(db, skip, limit)`), unlike
`register_endpoint` which had real work (secret generation). **Kept them anyway:**
1. **Consistency** — every route is Router → Service → Repository; no "special" routes to remember.
2. **Future logic has a home** — filtering to `is_active`, auth ("only my endpoints"), caching all land
   in the service later *without touching the router*.
3. **Router stays HTTP-only** — never imports the repo directly (that would leak the DB layer upward).

> 🎤 *"A pass-through service looks redundant, but it keeps layering uniform and gives business rules a
> predictable home — cost is one function, benefit is you never refactor the router when logic shows up."*

**Import aliasing** to avoid the service/repo name clash (both have `get_endpoint`):
```python
from app.db.repository import get_endpoint as get_endpoint_repo, get_endpoints as get_endpoints_repo
```

---

## 🔄 7. Hardening — Transactions & Rollback

A **transaction** = a group of DB ops treated as **all-or-nothing** (the **A** in ACID = Atomicity).
Either everything commits or nothing does.

**The problem:** if `db.commit()` throws (constraint violation, dropped connection, disk error), the
transaction is left **open and broken**. The session is *poisoned* — the next query on it throws a
confusing `PendingRollbackError` that looks unrelated to the real cause.

**`db.rollback()`** discards everything since the transaction began → session back to a clean slate.

**The pattern I added to `create_endpoint`:**
```python
try:
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)
    return endpoint
except SQLAlchemyError:
    db.rollback()   # 1. clean up the broken transaction
    raise           # 2. re-raise so the caller isn't lied to
```

Three decisions:
- **`rollback` THEN `raise`** (order matters) — clean the session first, *then* propagate.
- **Catch `SQLAlchemyError`, not bare `Exception`** — only handle DB errors I understand; a `KeyError`
  bug in my code shouldn't trigger a DB rollback.
- **Re-raise, don't swallow / return `None`** — the service/router must learn the write failed (→ a real
  500), otherwise it sails on as if it worked = silent data-loss, the worst kind of bug.

> 🫏 Writing an order in pencil = the transaction. `commit` = ink it permanently. `rollback` = erase the
> half-written order and start clean. What you never do is leave a smudge and write the next order on top
> (the poisoned session).

**Note:** couldn't test the failure path yet — no way to force a commit error until there's a `UNIQUE`
constraint (e.g. on `url`) to violate with a duplicate. Happy path still returns 201.

---

## 🫏 8. Donkey Translation (this day)

| 🫏 Donkey | 🛠️ Technical term |
|-----------|-------------------|
| "Show me all delivery cards, 50 at a time" | `GET /endpoints?skip&limit` (pagination) |
| "Show me card #5" | `GET /endpoints/{id}` (path param) |
| "No such card" instead of a blank card | `404` via `HTTPException` (not `200 + null`) |
| Reject a garbage address at the counter | `HttpUrl` on input → 422 |
| The receipt just mirrors what's filed | lenient `str` on output |
| Erase a half-written order, start clean | `db.rollback()` |
| All-or-nothing order | transaction / atomicity |

---

## ✅ Status & next

**Day 4 complete — read path + validation + rollback, all merged to `main`.**
1. ✅ Repository reads: `get_endpoints` (paginated), `get_endpoint` (by id, `Optional`)
2. ✅ Services: `list_endpoints`, `get_endpoint` (pass-through, kept for consistency)
3. ✅ Routes: `GET /endpoints` (query params, list model), `GET /endpoints/{id}` (path param, 404)
4. ✅ `HttpUrl` input validation (learned input-strict / output-lenient the hard way — a 500)
5. ✅ Hardening: transaction `rollback` on write failure

> Branches `feat/list-endpoints` + `feat/repo-rollback` → merged to `main`. ✅

**The endpoints resource is now full CRUD-minus-update+delete, hardened.** Next: the **core of the
project — events.**
- `Event` model + Alembic migration (event tied to an endpoint — first **foreign key / relationship**).
- `POST` to emit an event; durably store it *before* any delivery attempt.
- Sets up the real payoff: async delivery, retries, backoff, DLQ.

# 🗄️ DB Day 1 — Constraints & Schema Auditing

> **Part of the 7-day database course.** Lab = this project's own Postgres.
> **The one-line principle:** *If a rule must always be true, put it in the database.* Application checks can be
> bypassed — by a bug, another service, a migration script, or you in psql at 2am. A constraint cannot.

---

## A. Inspecting the schema

```sql
\d endpoints
\d events
\d delivery_attempts
```

Shows columns, types, nullability, defaults, indexes, and FK relationships **in both directions**
(`Foreign-key constraints:` = what this table points at; `Referenced by:` = what points at it).

### 5 findings

| # | Finding | Why it matters |
|---|---|---|
| 1 | `ix_endpoints_id` + `ix_events_id` duplicate the PK index | Postgres auto-indexes primary keys. Two indexes on one column = double write cost, zero benefit. |
| 2 | `status` is an unconstrained `varchar` | Nothing prevents `'deliverd'`. The worker **branches on this value**, so a typo → duplicate delivery. |
| 3 | 4 columns are `NOT NULL` with **no server default** | `default=` in SQLAlchemy is Python-side only. Any non-ORM writer (script, psql, bulk import) must supply the value or the insert fails. |
| 4 | `endpoints` has no `created_at` | Can't answer "when was this registered?" — and it's **unrecoverable** later. |
| 5 | `event_types` is a CSV | 1NF violation → needs a junction table. |

> **The schema records when each lesson landed.** `endpoints` (Day 2) and `events` (Day 5) carry the redundant
> id index; `delivery_attempts` (Day 6) doesn't. The mistake stopped mid-project.

**Deliberately NOT fixed:** no `UNIQUE` on `endpoints.url`. The same URL can legitimately hold multiple
subscriptions (different event types, different teams). Uniqueness probably belongs on `(url, event_type)` or
`(customer_id, url)` — neither expressible until `event_types` is properly modelled.
*"I considered it and decided it was wrong" beats both adding it blindly and never noticing.*

**Already correct:** both FK columns indexed (Postgres does **not** do this for you); nullable columns are
*meaningfully* nullable (NULL = "no response ever arrived" vs a number = "they answered"); timestamps are
`timestamptz`.

---

## B. Auditing data BEFORE constraining

```sql
SELECT status, COUNT(*) AS rows
FROM events
GROUP BY status
ORDER BY rows DESC;
```

**Result:** `delivered 12 | failed 8 | dead 7 | pending 4`

🚨 **`failed` is a legacy status.** Day 6 wrote it; Day 11 renamed it to `dead`; nothing migrated the old rows.
Had the constraint gone straight into a migration it would have **passed review, passed a fresh local DB, and
failed on deploy.**

> **The code moved on; the rows didn't.**

### Generic violator-finder — invert the constraint into a query

```sql
SELECT status, COUNT(*)
FROM events
WHERE status NOT IN ('pending', 'delivered', 'dead')
GROUP BY status;
```

This is the workflow when a migration fails on a real deploy: read the constraint out of the error message,
invert it into a `SELECT`, find out what's actually in there.

### Stuck-event finder (the `pending` orphans)

```sql
SELECT id, endpoint_id, created_at, now() - created_at AS stuck_for
FROM events
WHERE status = 'pending'
  AND created_at < now() - interval '10 minutes'
ORDER BY created_at;
```

The 4 `pending` rows are orphans from the **synchronous era** (pre-Redis), stranded when the architecture
changed underneath them. But the gap they expose is still live:

`pending` is the **only non-terminal state**, and it depends on an id existing somewhere in Redis (main queue,
processing list, or retry ZSET). If Redis loses it — restart without persistence, `enqueue_event` throwing
after `emit_event` committed, a flushed ZSET — **the event stays `pending` forever and nothing notices.**

`recover_orphans()` covers ids stranded in the *processing list*. Nothing covers events stranded in *`pending`
with no id anywhere.*

> **Any non-terminal state needs a timeout.** If a row can enter a state and never leave, you need a sweep —
> otherwise "stuck" and "in progress" are indistinguishable. → **a pending sweep is a real missing feature.**

---

## C. The constraint experiment

Run inside a transaction so nothing persists and Alembic stays in sync.

```sql
BEGIN;

-- ── A. prove the hole ────────────────────────────────────────────
INSERT INTO events (endpoint_id, payload, event_type, status, created_at)
VALUES ((SELECT MIN(id) FROM endpoints), '{"demo": true}', 'constraint.test',
        'totally-not-a-real-status', now())
RETURNING id, status;
-- → SUCCEEDS. Database happily stores a nonsense status.
-- Also proved finding #3: had to supply status + created_at BY HAND (no server defaults).

-- ── B. try to constrain ──────────────────────────────────────────
SAVEPOINT sp1;
ALTER TABLE events ADD CONSTRAINT ck_events_status
  CHECK (status IN ('pending','delivered','dead'));
-- → ERROR: check constraint "ck_events_status" ... is violated by some row

SELECT COUNT(*) FROM events;
-- → ERROR: current transaction is aborted, commands ignored until end of transaction block

ROLLBACK TO SAVEPOINT sp1;

-- ── C. delete MY test row, retry ─────────────────────────────────
DELETE FROM events WHERE status = 'totally-not-a-real-status';
SAVEPOINT sp2;
ALTER TABLE events ADD CONSTRAINT ck_events_status
  CHECK (status IN ('pending','delivered','dead'));
-- → FAILS AGAIN — the 8 legacy 'failed' rows
ROLLBACK TO SAVEPOINT sp2;

-- ── D. backfill FIRST (check before you write) ───────────────────
SELECT COUNT(*) FROM events WHERE status = 'failed';        -- verify: 8
UPDATE events SET status = 'dead' WHERE status = 'failed';  -- UPDATE 8

-- ── E. now it works ──────────────────────────────────────────────
ALTER TABLE events ADD CONSTRAINT ck_events_status
  CHECK (status IN ('pending','delivered','dead'));
-- → ALTER TABLE

-- ── F. hole closed ───────────────────────────────────────────────
SAVEPOINT sp3;
INSERT INTO events (...) VALUES (..., 'deliverd', now());
-- → ERROR: new row violates check constraint "ck_events_status"
--   DETAIL: Failing row contains (...)
ROLLBACK TO SAVEPOINT sp3;

ROLLBACK;   -- undo the entire experiment
```

### What each step taught

**`ADD CONSTRAINT` does TWO jobs:** (1) validate every existing row, (2) install the rule for future writes.
Job 1 fails → job 2 never runs, atomically. No half-applied state.
*At scale:* that validation is a full scan holding an `ACCESS EXCLUSIVE` lock — nothing reads or writes the
table meanwhile. Use `ADD CONSTRAINT ... NOT VALID` then `VALIDATE CONSTRAINT` later to avoid the outage.

**A failed statement poisons the transaction.** Every subsequent statement — even a harmless `SELECT` — errors
with `current transaction is aborted`. Postgres refuses to let you continue as if nothing happened, because
otherwise you could commit a transaction whose middle third silently didn't run.
> **This IS SQLAlchemy's `PendingRollbackError`** — same mechanism, different wrapper. It's why the repository
> calls `db.rollback()` **before** re-raising (Day 4).

**`SAVEPOINT` / `ROLLBACK TO SAVEPOINT`** = a bookmark inside a transaction. Rolling back to it discards work
after that point **and un-aborts** the transaction so you can carry on.

**Cleaning your own test data wasn't enough** (step C). A CHECK constraint is a claim about the table's
*current contents* — Postgres doesn't care who wrote a row or when. A teammate on a fresh database would never
have caught this locally.

**Order is non-negotiable:** data fix → *then* constraint. Alembic wraps each migration in a transaction, so
the pair is atomic — both land or neither does.

**Two different error shapes:**
- `ALTER TABLE` → *"violated by some row"* — deliberately vague; millions could violate, naming one is useless
- `INSERT` → `DETAIL: Failing row contains (...)` — exactly one candidate, so showing it is useful

**Postgres has transactional DDL.** `ROLLBACK` undid the `ALTER TABLE` *and* the `UPDATE` *and* both `INSERT`s
together. **MySQL cannot do this** — DDL there is auto-committing and irreversible.

**Postgres rewrites `IN` as `= ANY (ARRAY[...])`** when it stores the constraint. Same meaning, different text
when you read it back with `\d`.

**Disconnecting = implicit rollback.** An uncommitted transaction belongs to its connection; when the
connection drops, Postgres discards everything. There's no reattaching.
> **Only `COMMIT` makes anything real.** Not "the statement ran," not "psql printed UPDATE 8."
> The upside: an interrupted transaction can never leave the database half-changed.

---

## D. Verification

```sql
SELECT status, COUNT(*) FROM events GROUP BY status ORDER BY 2 DESC;  -- 'failed | 8' is BACK
SELECT id, status FROM events WHERE event_type = 'constraint.test';   -- 0 rows
\d events                                                             -- no Check constraints section
```

---

## E. Ordering & pagination

```sql
SELECT * FROM events ORDER BY id DESC LIMIT 2;                -- last 2 INSERTED
SELECT * FROM events ORDER BY created_at DESC LIMIT 2;        -- most recent by TIMESTAMP
SELECT * FROM (SELECT * FROM events ORDER BY id DESC LIMIT 2) t ORDER BY id;   -- last 2, ascending
SELECT * FROM events ORDER BY id DESC LIMIT 2 OFFSET 2;       -- skip 2
```

**A table has no inherent order.** Without `ORDER BY`, Postgres returns rows in whatever order is cheapest —
roughly insertion order *until* an `UPDATE` rewrites a row elsewhere, `VACUUM` reclaims space, a parallel scan
runs, or an index scan is chosen. **Not random — worse:** it works in testing and breaks later.
> `ORDER BY` is the only thing that defines order. No `ORDER BY`, no guarantee. Ever.

`id` = insertion order (sequence). `created_at` = app-set. They usually agree but diverge on backfills or
concurrent inserts. *"What did my worker just write?"* → `id`. *"What happened most recently?"* → `created_at`.

**`OFFSET` doesn't skip cheaply** — it generates and discards rows, so `OFFSET 100000` builds 100k rows first.
This is what `get_events(db, skip, limit)` compiles to. Fine for page 1, degrades badly on deep pagination.
Scalable alternative = **keyset pagination**: `WHERE id < :last_seen_id ORDER BY id DESC LIMIT 20` — jumps
straight there via the index.

---

## Fix list (→ next: apply via Alembic)

| # | Change | Priority |
|---|---|---|
| 1 | Drop `ix_endpoints_id`, `ix_events_id` | do it |
| 2 | `CHECK (status IN ('pending','delivered','dead'))` **+ backfill `failed` → `dead` first** | do it |
| 3 | `server_default` on `is_active`, `status`, `created_at`, `attempted_at` | do it |
| 4 | `CHECK (attempt_number > 0)` | do it |
| 5 | `created_at` on `endpoints` | do it |
| 6 | `payload` → `JSONB` (indexable; `json` re-parses on every read) | later |
| 7 | `event_types` → junction table | DB Day 4 |
| 8 | UNIQUE on `url` | **deliberately skipped** |

### Gotchas for the migration

- **`server_default` takes a SQL expression, not a Python value:** `text("true")` not `True`; `func.now()` not
  `datetime.now()` (so the *database* evaluates it per-row, rather than baking in the migration's timestamp).
- **`__table_args__` must be a tuple** — the trailing comma is load-bearing.
- **Always name constraints.** Auto-generated names differ between databases and make the downgrade
  unwriteable.
- **Alembic doesn't reliably autogenerate CHECK constraints** — expect to add `op.create_check_constraint(...)`
  by hand.
- **The `failed` → `dead` backfill won't be generated at all** — add `op.execute(...)` **above** the constraint.
- **`created_at NOT NULL` on a populated table** is the Day 8 problem again: needs a `server_default` (or
  backfill-then-drop) so existing rows get a value.

---

## ❓ Interview cheat-sheet

- **"Why put rules in the database instead of the app?"** — App code can be bypassed by another service, a
  script, or a console session. A constraint is enforced on every write from every client, forever.
- **"What happens when you add a constraint to an existing table?"** — Postgres validates every existing row
  first and rejects the whole statement if any violate. On big tables that's a full scan under an exclusive
  lock, so use `NOT VALID` + `VALIDATE CONSTRAINT` to split it.
- **"A migration passed locally and failed in production — why?"** — Almost always data. Production has
  history your fresh dev database doesn't. Audit with the inverted constraint before you write the migration.
- **"What's a savepoint?"** — A bookmark inside a transaction. Lets you roll back part of it and continue —
  including recovering from the aborted state a failed statement causes.
- **"Does `ROLLBACK` undo schema changes?"** — In Postgres, yes: DDL is transactional. In MySQL, no.
- **"Why does `SELECT ... LIMIT 2` without `ORDER BY` worry you?"** — A table is an unordered set. Row order is
  whatever's cheapest and changes as data moves. Without `ORDER BY` there's no guarantee at all.

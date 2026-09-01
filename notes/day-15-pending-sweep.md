# 🧹 Day 15 — The pending sweep (reconciliation)

> **What I built:** a background loop that periodically asks Postgres *"which events are still owed but have
> gone quiet?"* and puts them back on the queue — or dead-letters them if they're too old to be worth sending.
> `pending` is no longer a status an event can be stranded in forever.

---

## 🗣️ Plain-language version — start here when revising

### The problem

An event's **truth** lives in Postgres (`status = 'pending'`). Its **liveness** lives in Redis (an id sitting in
a queue). If Redis loses that id, Postgres still says `pending`, no error fires, and the event waits forever.

Nothing was watching for this. I found **four real orphans** in my own database during a DB lesson: events
**1, 8, 9** (never enqueued at all, zero attempts) and event **11** — the corpse of the Day-9 infinite loop,
167 attempts in 58 seconds. They'd been sitting there for six weeks.

### The fix, in one sentence

> **A reconciler is a thermostat.**

A thermostat does three things, forever:

1. What *should* be true? — 21°C
2. What *is* true? — 18°C
3. Different? — turn on the heat

It never asks **why** the room got cold. Window, door, cold snap, broken heater — it doesn't investigate. It
sees a gap and closes it, then does it again.

My sweep: *"every pending event should have an id waiting in Redis"* vs *"some pending events have gone
silent"* → put them back. Every 60 seconds. Never asks why the id disappeared.

### Why that beats fixing the causes

I could enable Redis persistence, add an outbox, make `promote_due_retries` atomic with Lua. All worth doing.
But a reconciler **doesn't need to know why drift happened** — so it fixes all of those *and* the cause I
haven't thought of yet.

**Fixing bugs individually addresses the ones you found. A reconciler addresses the category.**

---

## Why derived state drifts

Redis isn't caching event *data* — but the queue's contents are **derivable** from Postgres:

> *"ids needing delivery" = "events with status pending"*

So the queue is a **materialised view of a query over the DB** — a second copy of a fact that lives
authoritatively somewhere else. Copies drift. Three ways, all visible in my own code:

| # | Cause | Where |
|---|---|---|
| 1 | The copy is destroyed | Redis restart without persistence, eviction, `FLUSHDB` |
| 2 | The two writes aren't atomic | `emit_event()` commits to Postgres, **then** `enqueue_event()` pushes to Redis. Two systems, no shared transaction. Crash between = row exists, queue entry never did |
| 3 | Multi-step updates to the copy aren't atomic | `promote_due_retries()` does `zrem` then `lpush`. Die between them and the id is removed from the schedule and never added to the queue |

**And drift is silent.** No exception, no failed request, no log line. The system quietly does less than it
should while looking perfectly healthy. My four orphans sat for six weeks and I only found them by accident.

> Failures that announce themselves are easy. The ones that reduce output *without changing behaviour* survive
> for months.

---

## The design — two thresholds, not one

```
 |<-- in flight -->|<-- stale: re-queue -->|<-- expired: dead, never deliver -->|
 0              15 min                    24 h                                  →
```

### Why not just "re-queue everything pending"?

Because **an event being delivered right now is also `pending`.** A naive sweep re-queues a healthy in-flight
event and you get a **double delivery** — the exact thing the whole system is built to avoid.

Time is the only cheap discriminator I have, so the threshold has to exceed any legitimate in-flight window.

### Deriving the threshold instead of guessing

Worst legitimate gap between a pending event's attempts = `MAX_DELAY` (60s) + HTTP timeout (5s) ≈ **65 seconds**.
15 minutes is ~13× that margin.

**Being late costs nothing. Being wrong costs a duplicate delivery.** When one side of an error is cheap and
the other is expensive, bias hard toward the cheap side.

`STALE_AFTER` is **load-bearing** — it is the *only* thing preventing double delivery. Never tune it toward zero.

### Why an upper bound too

Delivering a six-week-old `payment.succeeded` is worse than not delivering it — the receiver processes a stale
event as if it just happened. Past 24h → dead-letter, never send. (Stripe gives up at ~3 days.)

### Two thresholds, two different columns — this looks inconsistent, it isn't

| Question | Measures | Column |
|---|---|---|
| **Stale** — has anyone touched this lately? | **silence** | `COALESCE(max(attempted_at), created_at)` |
| **Expired** — is this still worth sending? | **age** | `created_at` |

An event created six weeks ago but attempted twenty minutes ago is **not stale** — but it's still too old to
deliver. Different questions, so different columns.

---

## 🐛 The bug I caught *during* the design

My first version measured staleness from `created_at`. It's broken:

1. Sweep re-queues a stale event
2. Worker retries it, it fails, a retry gets scheduled — status **stays** `pending` (correct)
3. `created_at` **never changes**
4. 60 seconds later the sweep matches it *again* and enqueues a **second copy** while the first retry is still
   scheduled → **double delivery**

Measuring from **last activity** self-heals: once an attempt happens, `attempted_at` becomes recent and the
event drops out of the stale set on its own until it goes quiet again.

`created_at` can't fix this, because it never changes. That's what forced the join.

---

## The query

```sql
SELECT e.* FROM events e
LEFT JOIN delivery_attempts a ON a.event_id = e.id
WHERE  e.status = 'pending'
GROUP BY e.id
HAVING COALESCE(max(a.attempted_at), e.created_at) < :cutoff
ORDER BY COALESCE(max(a.attempted_at), e.created_at)
LIMIT :limit;
```

### Three ideas doing the work

**1. `LEFT JOIN`, not `JOIN`.** An inner join only keeps events with at least one matching attempt row.
Events 1, 8, 9 have **zero** attempts — an inner join drops exactly the orphans this feature exists to rescue.

**2. `WHERE` vs `HAVING`.** `status = 'pending'` tests one row at a time → applied *before* grouping → `WHERE`.
`max(attempted_at) < cutoff` is a property of a whole group and doesn't exist until after grouping → `HAVING`.

> **`WHERE` filters rows. `HAVING` filters groups.**

**3. `COALESCE`.** `max()` is `NULL` when there are no attempt rows. And in SQL **`NULL < anything` is `NULL`,
not false** — `HAVING` keeps only rows where the condition is *true*, so without COALESCE every zero-attempt
event silently vanishes.

### ⚠️ Two independent silent failure modes

Using an inner join, **or** forgetting COALESCE. Either one loses exactly the events I care about most, with no
error and no clue. That's why NULL semantics are worth understanding rather than memorising.

### `LIMIT` without `ORDER BY` is almost always a bug

SQL has **no default order**. `LIMIT 100` returns *some* 100 rows — whichever are cheapest to reach, which
depends on physical layout, index vs seq scan, whether autovacuum reorganised pages. Identical queries can
return different sets.

With 250 stranded events and a batch of 100:

- **With `ORDER BY`** → a queue at a counter. Serve the front 100; next tick the next 100 are at the front.
  Everyone gets served, and I can predict when.
- **Without** → a mob at a counter. Each tick grabs 100 arbitrary people. Someone at the back can be passed
  over **every single round, forever**. Nothing errors. That's **starvation**.

### `GROUP BY` does two jobs

It enables `max()`, **and** it collapses the join's row multiplication. Without it, the outer join returns
**event 11 one hundred and sixty-seven times** — once per attempt row.

---

## 🐛 Bugs I made writing this (and what they rhyme with)

| Bug | What happened | Rhymes with |
|---|---|---|
| `.limit(100)` hardcoded instead of `.limit(limit)` | A parameter I accept but ignore is **a lie in the signature**. Invisible in testing because I hardcoded *the same value as the default* | — |
| `list2.append(enqueue_event(event.id))` | `enqueue_event` returns `None`. The list filled with `[None, None, ...]` | **Day 7** — `return enqueue_event(...)` in the events route |
| `return` inside the `for` loop | Returned after the **first** event; 8, 9 and 11 silently untouched. Didn't crash — just produced a plausible-looking `expired = [1]` | **Day 11** — `return` inside the `while` in `replay_dead_letters` |
| `list1` / `list2` | Docstring said `(requeued, expired)`; the code returned them **backwards**. Bad names don't just hurt readability — they make a whole class of ordering bug *unwritable* once fixed | — |
| `except Exception as e: print("Error")` | Bound the exception and never used it. Knowing *that* it failed with no *why* is worse than no log | — |

> **A function called for its side effect usually returns nothing useful.** `dead_letter`, `enqueue_event`,
> `lpush` — call them to make something happen, then append the thing you actually want.

---

## Wiring it into the worker

The sweep rides on the heartbeat Day 10 already created (`blmove(timeout=1)`), same as `promote_due_retries()`.
No new process to deploy or monitor.

### The interval guard

```python
last_sweep = time.monotonic() - SWEEP_INTERVAL   # before the loop

now = time.monotonic()
if now - last_sweep >= SWEEP_INTERVAL:
    last_sweep = now       # reset BEFORE the work
    ...
```

**`time.monotonic()`, not `time.time()`.** Third appearance of the two-clocks lesson (Day 8 `perf_counter`,
Day 13 wall-vs-monotonic). Wall clock can jump *backwards* on an NTP correction — a negative difference would
silently stop the sweep until the clock caught up. Monotonic clocks only move forward.

**Never `time.sleep(60)`.** That stalls the whole loop and every incoming event waits up to a minute. I'm not
creating a schedule — I'm checking a clock on a tick that already exists.

**`last_sweep` is initialised outside the loop because it's the loop's memory.** Everything else (`event_id`,
`db`, `now`) is per-tick scratch. Initialise it *inside* and it resets every tick, `now - last_sweep` is always
~0, and the sweep never runs — with no error at all. **Where you initialise a variable decides its lifetime.**

Initialising to `monotonic() - SWEEP_INTERVAL` makes it sweep on the **first** tick, which pairs nicely with
`recover_orphans()` at boot.

**Reset `last_sweep` before the work, not after.** If I assign after and the sweep raises, the assignment never
runs — so the next tick, one second later, tries again. And again. A failing sweep becomes a tight loop
hammering the database. Resetting up front costs one skipped cycle instead.

### Position and blast radius

It must sit **above** `if event_id is None: continue`. Below it, the `continue` fires on every idle tick and the
sweep never runs — precisely when it's needed most, on a quiet system with stranded events.
*(Same shape as the Day-10 bug where `promote_due_retries()` sat outside the loop.)*

It also needs its **own session** (`SessionLocal()` + `finally: db.close()`) — the per-event session doesn't
exist yet at that point in the tick — and its own broad `except Exception`, because everything above
`db = SessionLocal()` is outside the per-event try block. An unprotected sweep there would kill the worker
permanently on one bad database moment.

---

## ✅ How I verified it

**Test A — the expired branch.** Started the worker; the first tick printed
`Sweep expired 4 events: [1, 8, 9, 11]`. All four flipped to `dead` in Postgres and appeared in `webhook:dead`.
Six weeks late, event 11 finally reached a terminal state.

**Test B — the re-queue branch, proved by accident.** Event 43 was created at **14:19:19**. Its neighbours 42
and 44 were delivered in 20 and 45 seconds. Event 43's one and only delivery attempt landed at **14:58:32** —
**39 minutes later**, because its id had been lost from Redis and the sweep found it on the next worker start.

Three things that proved, which Test A couldn't:

- the **re-queue branch** executes correctly — the branch that is the entire point of the feature
- **expiry classified correctly** — 43 is *not* in the DLQ, so it took the `else` path
- **no double delivery** — `attempts = 1`. Repeated sweeping would have left multiple attempt rows

> The unplanned test was the better one, because I didn't shape the conditions to make it pass.
> (Same lesson as Day 13's tampered secret and Day 14's httpbin: a test that can't distinguish a correct
> implementation from a broken one isn't a test.)

**Habit worth keeping:** prove the query in `psql` first, then translate to SQLAlchemy. And `print(query)` before
`.all()` — reading the emitted SQL would have caught the Day-9 infinite loop in thirty seconds.

The generated SQL also shows `%(status_1)s`-style **bound parameters** — values are sent to the driver
separately and never parsed as SQL. Injection-proof by construction.

---

## ⚠️ Known limits

- **Single-worker assumption.** Three workers would each sweep, each find the same stranded events, and each
  re-queue them → triple delivery. Same assumption `recover_orphans()` already makes. Needs a lock or a
  dedicated sweeper process.
- **The sweep only runs while a worker is alive.** Event 43's 39-minute gap wasn't a bug — nothing was running.
  A fully-down system is reconciled by nothing.
- **Nobody is watching what the sweep finds.** The most interesting one: a reconciler that silently repairs
  things forever **hides the bug that keeps causing them**. If the sweep starts rescuing fifty events a day,
  something upstream is broken and I'd never know. Real systems alert on reconciler activity — **a busy repair
  loop is a symptom, not a success.**
- **`recover_orphans()` still only covers the processing list**, not the retry ZSET. The sweep now covers that
  gap from the DB side, so this is less urgent than it was.

---

## 🎤 Interview cheat-sheet

**"How do you make sure no event is ever lost?"**
> Three layers. Durability — the event is committed to Postgres *before* anything is queued, so the DB is
> always the source of truth. Recovery — an atomic `BLMOVE` into a processing list plus startup orphan
> recovery, so a worker killed mid-delivery doesn't lose the id. And reconciliation — a periodic sweep that
> re-derives outstanding work from the database, so even losing Redis entirely is recoverable.

**"What's a reconciler?"**
> A loop that compares desired state to actual state and closes the gap, forever, without caring why they
> diverged. It's the Kubernetes controller model. The value is that it repairs failure modes you haven't
> identified yet — you fix the category rather than the instances.

**"Why not just re-queue everything that's pending?"**
> Because an event being delivered right now is also pending, so you'd double-deliver live traffic. You need a
> silence threshold larger than the longest legitimate gap between attempts — for us that's max backoff plus
> the HTTP timeout, about 65 seconds, so 15 minutes gives a comfortable margin.

**"Why a LEFT JOIN?"**
> Because events that were never enqueued have zero attempt rows, and those are exactly the ones I'm hunting.
> An inner join silently drops them. Same with `COALESCE` — `NULL < cutoff` is `NULL`, not true, so without the
> fallback they'd fail the `HAVING` and disappear. Two independent ways to lose precisely the rows that matter.

**"What would you do differently at scale?"**
> Multiple workers break the single-sweeper assumption, so either a distributed lock or a dedicated reconciler
> process. I'd also emit a metric on how many events each sweep rescues and alert on it — a reconciler doing a
> lot of work is telling you something upstream is broken, and right now that signal goes nowhere.

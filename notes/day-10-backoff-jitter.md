# 📘 Day 10 — Exponential Backoff + Jitter (retries become *polite*)

> **What I built:** turned "retry immediately" into "retry *later*, waiting longer each time." A failed
> event no longer goes straight back on the queue — it's scheduled into a Redis **sorted set (ZSET)**
> with a future timestamp, and a poller promotes it back to the main queue once its time arrives. Plus
> **jitter** so many simultaneous failures don't retry in a synchronized stampede.

---

## 🧠 The core concept: a Redis ZSET (sorted set)

A **sorted set** holds **unique members**, each with a number called its **score**, and Redis keeps them
**permanently sorted by score**. Think: a *set* (unique members) crossed with a *leaderboard* (everyone
ranked by a number).

Three defining properties:
1. **Members are unique** — re-adding a member just *updates its score*, no duplicate.
2. **Every member has a score** (a float).
3. **Always kept sorted by score** → range queries by score are fast (O(log n)).

The superpower: **query by score range cheaply.** Put different things in the score and the ZSET becomes
different tools:

| score = … | ZSET becomes… |
|-----------|---------------|
| game points | a leaderboard |
| **a timestamp** | **a schedule / delayed queue** ← our use |
| a priority | a priority queue |

Core ops: `ZADD key <score> <member>`, `ZRANGEBYSCORE key <min> <max>`, `ZREM key <member>`,
`ZPOPMIN`, `ZSCORE`.

**Why a ZSET and not a list?** Our main queue (`webhook:delivery`) is a **list** — pure FIFO, "take the
next one," no notion of *time* or *priority*. There's no way to say "but not until 3:00:04." The ZSET
adds exactly that missing dimension: **a number you can range-query on.** Make that number a timestamp →
you have a scheduler.

⚠️ **Gotcha:** the CLI is `ZADD key score member` (score first) but redis-py takes a **`{member: score}`
dict** (member first): `client.zadd("retries", {"42": 1784416896})`. They read in opposite orders.

---

## 🧩 The pattern: "delayed job queue with a ZSET"

A well-documented pattern. Generic template:

```
# schedule to run later:   ZADD <zset>  <run_at_timestamp>  <job_id>
# poller (runs often):     due = ZRANGEBYSCORE <zset> -inf <now>
#                          for job in due: if ZREM <zset> job: push job to the real work queue
```

The whole trick: **the score is the time it should run**, so a range query `[-inf, now]` is exactly
"what's due right now." Future-timestamped items simply aren't in that range yet — they wait.

### My two functions (`redis_client.py`)

```python
RETRY_ZSET = "webhook:retries"    # (I named it DELAYED) score = deliver-after unix ts, member = event_id
BASE_DELAY = 1.0
MAX_DELAY  = 60.0

def schedule_retry(event_id: int, attempt_count: int) -> float:
    delay = min(MAX_DELAY, BASE_DELAY * (2 ** (attempt_count - 1)))   # 1,2,4,8,16 capped
    delay = delay * (0.5 + random.random() * 0.5)                     # equal jitter
    client.zadd(RETRY_ZSET, {str(event_id): time.time() + delay})
    return delay

def promote_due_retries() -> None:
    now = time.time()
    due = client.zrangebyscore(RETRY_ZSET, "-inf", now)
    for event_id in due:
        if client.zrem(RETRY_ZSET, event_id):     # claim-guard: only the remover enqueues
            client.lpush(QUEUE_KEY, event_id)
```

The `if zrem(...)` guard matters: if two pollers see the same due id, only the one that *actually removes*
it gets a truthy return → it can't be promoted twice.

---

## ❓ Questions I worked through (and the answers)

### Q: How would a programmer know `schedule_retry` needs a *second* argument (`attempt_count`)? The canonical template only takes a fixed delay.
You **don't** get it from the template — you *reason it into existence* from your requirement.
- The template does "run later" with a *fixed* delay. My requirement adds a rule: **each retry waits
  longer** (exponential backoff).
- Follow the formula `delay = base * 2**(n-1)` → it needs `n` (which attempt this is).
- **The heuristic:** *a function's parameters are the information its body needs but cannot produce on its
  own.* Walk each line: `event_id` (must be given), `n` (must come from somewhere), `base`/`random` (it
  makes those itself → not params).
- `n` "from somewhere" is a design choice: **pass it in** (the worker already computed
  `attempts = count_delivery_attempts(...)`, so hand it over — cheap, keeps `schedule_retry` decoupled
  from the DB) vs **compute inside** (simpler signature, but needs a DB session + redundant query).
  Chose pass-in. That reasoning *is* the engineering skill; the template is just scaffolding.

### Q: Why is the jitter `delay * (0.5 + random.random() * 0.5)`?
`random.random()` → `[0,1)`. So:
```
random.random() * 0.5      → [0.0, 0.5)      the random part (0–50%)
0.5 + random.random()*0.5  → [0.5, 1.0)      shifted up (50–100%)
delay * (that)             → [0.5·delay, delay)   50–100% of the backoff
```
It's **fixed floor + random spread**:
- `0.5 +` = **guaranteed floor** — always wait ≥ half the backoff (preserves the "give the server room"
  purpose; never retry instantly).
- `random.random()*0.5` = **the spread** — randomizes the other half, which is what breaks the
  **thundering herd** (1000 events that all computed 4s get scattered across [2s,4s) instead of all
  firing at t+4).
This is AWS "**equal jitter**." Alternative "**full jitter**" = `random.uniform(0, delay)` → spreads
harder but can retry near-instantly (loses the floor). The `0.5`s are a tunable dial (`0.7 + rand*0.3` =
70% floor).

### Q: Why did `blmove`'s timeout have to change from 0 to 1?
With `timeout=0`, `blmove` **blocks forever** waiting on the main queue. If a retry is sitting in the
ZSET but no *new* events arrive, the worker is **idle, blocked forever** — it never loops back to run
`promote_due_retries()`, so the retry's time passes and nothing happens. `timeout=1` makes the worker
wake at least once a second to run the promoter. The 1s heartbeat *drives the scheduler*. (Blocking is
still great for *new* events — instant pickup; we just cap it at 1s so the *time-based* ZSET source also
gets serviced.) Must also handle the `None` `blmove` returns on timeout → `continue`.

### Q: Why is `webhook:retries` empty after an event fails *permanently*?
The ZSET is a **transient waiting room**. An id is only in it *between* a failed attempt and when it
becomes due; `promote_due_retries` **ZREMs** it on the way to re-delivery. And the **final** attempt hits
the `else` → `update_event_status("failed")`, which does **not** call `schedule_retry` — so nothing new
is added. Net: empty by the time it's permanently failed. **Correct.** To catch it non-empty, look during
a wait window (best after attempt 4's ~4–8s delay).

### Q: What is that score like `1.7844168968085814e+9`?
Scientific notation for a big float ≈ `1784416896.8` — a **unix timestamp** (seconds since 1970-01-01).
It's `time.time() + delay`: the exact moment the id becomes "due." The score is the appointment time; the
ZSET is the waiting room.

---

## ✅ Verified
Always-failing endpoint (`httpbin/status/500`), one event → attempts spaced out with growing delays:
`retry in 0.6s → 2.0s → 2.4s → 4.7s → failed permanently after 5 attempts`. Caught the id mid-wait in the
ZSET: `zrange webhook:retries 0 -1 withscores` → `"16"` with a future timestamp, then it vanished when
promoted. The 5 attempts now spread over ~10s instead of a blink.

## ⚠️ Known limitation (future hardening)
The retry ZSET lives **only in Redis**, and `recover_orphans` reclaims only the *processing* list — not
the ZSET. If Redis died, scheduled retries would be lost and those events would sit `pending` forever.
Real fixes: a DB-backed schedule, or a periodic **pending-sweep** (re-enqueue events stuck `pending` past
a threshold). Not fixing now, just aware.

---

## 🎤 Interview cheat-sheet (how to talk about this project)

**This project = the canonical system-design question.** They won't ask me to *type* this code, but the
knowledge maps straight onto:
- **System-design round:** "Design a webhook delivery / notification service / job queue / rate limiter."
- **Project deep-dive:** "Walk me through the hardest bug / why did you choose X?"
- **ML infra (my target role):** async inference queues, retrying flaky LLM/model-API calls with backoff,
  DLQs for failed batch jobs — same patterns.

Likely Q&As:

- **"How do you handle a receiver that's down?"** → Store the event durably first (`pending`), deliver
  async off the request path via a Redis queue + worker, retry on failure with a bounded cap.

- **"Why exponential backoff, and why jitter?"** → Backoff (1,2,4,8s) gives a struggling server
  increasing breathing room instead of hammering it. Jitter randomizes the delay so many events that
  failed at the same instant don't retry in a synchronized spike (**thundering herd**) that re-crushes
  the server the moment it recovers.

- **"What happens if a worker crashes mid-delivery?"** → I use a **reliable queue**: `BLMOVE` atomically
  moves the id to an in-flight `processing` list; it's only `LREM`'d after success (ack). A crash leaves
  the id in `processing`, and startup **recovery** re-queues it. Under a plain `BRPOP` the id would be
  deleted on pop and lost.

- **"Could an event be delivered twice?"** → Yes — this is **at-least-once**. If a crash lands after
  delivery but before the ack, recovery re-delivers. The fix is **idempotency** (dedupe on the receiver
  side / an idempotency key). True exactly-once is effectively impossible across a network; at-least-once
  + idempotency is the standard.

- **"Where do permanently-failing events go?"** → A **dead-letter queue** (separate list) after N tries,
  with manual/self-serve replay. Stops auto-hammering a dead endpoint while never losing the event.

- **"How do you schedule a delayed retry in Redis?"** → A **sorted set** with the score = the
  deliver-after timestamp; a poller promotes everything with score ≤ now onto the work queue.

- **"How would this scale to millions of events / many workers?"** → Multiple worker processes (the queue
  hands each id to exactly one), per-worker processing lists or visibility timeouts for safe recovery,
  and a circuit breaker to stop wasting attempts on a persistently-dead endpoint.

**One-liner to open with:** *"It's a Stripe-style webhook delivery service — durable storage, async
delivery via a Redis work queue, retries with exponential backoff + jitter, a reliable queue that
survives worker crashes, and a dead-letter queue for permanent failures."*

---

## ⏭️ Next — Day 11
Dead-letter queue: park permanently-`failed` events on `webhook:dead` and add a manual replay path — the
re-attempt itself is the "is the subscriber back up?" check (no health probe).

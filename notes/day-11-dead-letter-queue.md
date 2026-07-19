# 📘 Day 11 — Dead-Letter Queue + Replay (quarantine, don't discard)

> **What I built:** when an event exhausts its 5 retries it no longer just gets marked `failed` and
> forgotten — it's **dead-lettered**: pushed onto a separate `webhook:dead` list and marked `dead`.
> Nothing auto-drains that list; a human triggers **`POST /dlq/replay`** to move dead events back onto the
> main queue for another attempt. The re-attempt itself is the "is the subscriber back up?" check.

---

## 🧠 The concept

A **dead-letter queue (DLQ)** is a quarantine for messages that failed *every* retry. Instead of losing
them (or retrying a dead endpoint forever), you park them somewhere findable and stop the automatic
attempts.

```
retries exhausted → LPUSH id to webhook:dead + status = "dead"      (park)
                          │  nothing auto-drains webhook:dead
                          │
             human decides subscriber is fixed → POST /dlq/replay
                          │
             LMOVE ids back to webhook:delivery + status = "pending" (replay)
                          │
             worker re-attempts → fixed? delivered ✅   still broken? back to dead ☠️
```

## Part 11a — parking exhausted events

- `redis_client.py`: `DEAD_KEY = "webhook:dead"` + `dead_letter(event_id)` → `LPUSH DEAD_KEY event_id`
  (identical shape to `enqueue_event`, just a different destination key).
- Worker's exhausted `else` branch: `dead_letter(event_id)` **then** `update_event_status("dead")`, then
  the shared `lrem` ack — so the id is durably in the DLQ *before* it leaves the processing list.

### The status model gained a state
| status | meaning |
|--------|---------|
| `pending` | will keep being attempted |
| `delivered` | success ✅ |
| `dead` | retries exhausted → **quarantined in the DLQ**, awaiting manual replay ☠️ |

Chose a distinct `dead` (over reusing `failed`) so the status unambiguously means "in the DLQ."

### Why store "it's dead" in BOTH the list and the DB status? (not redundant)
| | `status = "dead"` (DB) | `webhook:dead` (Redis list) |
|---|---|---|
| Answers | *"what state is **this** event in?"* | *"**which** events await replay?"* |
| Use | per-event lookup / reporting | a drainable **worklist** for batch replay |
| Role | durable source of truth | ergonomic handle for the replay action |

Same fact **indexed two ways for two jobs** (mirrors Day 9's processing-list vs DB-row). You can't
efficiently "replay all dead" from the status column (full table scan), nor answer "is 42 dead?" from the
list (scan the list).

## Part 11b — replay

- **`app/services/dlq.py`** → `replay_dead_letters(db) -> list[int]`: `while True` drain loop, `LMOVE`
  each id `DEAD_KEY → QUEUE_KEY`, `int()` it, `update_event_status(pending)`, collect; `return replayed`
  **after** the loop.
- **`app/api/routes/dlq.py`** → `POST /dlq/replay`, `response_model=ReplayResult`, returns
  `{"replayed": [...], "count": N}`. Wired into `main.py`.
- **`ReplayResult`** schema (`replayed: list[int]`, `count: int`) — no `from_attributes` (it's a plain
  dict I build, not an ORM row).

### 🐛 Bugs caught in review
- **`return replayed` was *inside* the `while` loop** → replayed only the first id, left the rest stuck.
  Had to dedent it to run after the loop drains fully.
- **`lmove` returns a `str`** (`decode_responses=True`) → had to `int()` it before `update_event_status`
  (integer column) and before appending (the `-> list[int]` contract).
- **Forgot to wire the router in `main.py`** → the endpoint 404s *silently*. Easy to miss.

### Why LMOVE, not RPOP?
`RPOP` deletes first, pushes second — a crash in the gap loses the id. `LMOVE` **atomically** moves it, so
it's always in exactly one list. This matters *more* for the DLQ than the normal queue: **a dead event is
precious** — the producer already POSTed it once, got its `201`, and will *never* re-send it. So the DLQ
entry is the last *actionable handle* on that delivery. (Backstop: the DB row is still `status = dead`, so
worst case you'd recover it via a table scan — but LMOVE spares you that.)

## ✅ Verified
Event 18 (500 endpoint) exhausted → landed in `webhook:dead` + `status = dead`. `curl -X POST
/dlq/replay` → `{"replayed":[18],"count":1}` → worker re-attempted → still 500 → **back to dead**.
Round-trip proven: **park → replay → re-attempt → re-park.**

### ⚠️ Option A limitation, seen firsthand
We chose **Option A** (replay = re-enter the flow, no counter reset) over **Option B** (a resettable
`retry_count` column). Because `count_delivery_attempts` counts *all* rows, each replay adds just one
attempt — the count climbed `5 → 6 → 7` across replays. So replay is "one more shot," not a fresh budget
(matches Stripe's "Resend"). Fresh-budget-per-replay would need the Option B column refactor.

## 💡 Smaller lessons
- **Return `None` vs return data:** a function returns `None` when its value is a *side effect*
  (`recover_orphans` just logs); it returns data when a *caller must act on the result* (`replay` feeds
  the route's response). Derive the return type from "what does the caller need back?"
- **Loop choice:** draining an unknown-count source → `while True` + sentinel `break` (or the walrus
  `while (x := ...) is not None:`), **not** a `for` over a pre-counted `LLEN` (racy — the list mutates as
  you drain and others push).
- **`response_model` on a non-ORM response** — still worth it (Swagger docs + output validation), just no
  `from_attributes` since it's a plain dict.

## 🎤 Interview angle
"How do you stop retrying a permanently-dead endpoint without losing the event?" → **DLQ**: park after N
tries, quarantine (stop auto-retrying), and expose a **manual/self-serve replay**. At company scale this
is wrapped in auto-disable + owner notifications + a durable event-log pull fallback; nobody hand-inspects
individual dead subscribers.

## ⏭️ Next — Day 12
Idempotency (guard the at-least-once double-delivery from crash recovery / replay) + the missing reads
`GET /events` and `GET /events/{id}` (so you can actually observe status + attempt history). Last day of
Phase 7.

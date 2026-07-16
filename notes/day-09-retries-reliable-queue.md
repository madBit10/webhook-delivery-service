# 📘 Day 9 — Retries + Reliable Queue (the system stops losing events)

> **What I built:** two things. **(9a)** retry-on-failure with a bounded cap — a failed delivery is
> re-queued and retried up to `MAX_ATTEMPTS`, then given up on. **(9b)** the *reliable queue* — upgraded
> `BRPOP` → `BLMOVE` so a worker crash mid-delivery can no longer orphan an event, plus startup recovery
> to reclaim anything a crash left behind. This is the day the system became genuinely crash-safe.

---

## Part 9a — Retry on failure

### The status model (a design decision)
Once you retry, `failed` becomes ambiguous — "failed this try" vs "gave up"? So I nailed down three
meanings:

| status | meaning |
|--------|---------|
| `pending` | not yet delivered — **will keep being attempted** (also covers "between retries") |
| `delivered` | success ✅ (terminal) |
| `failed` | retries **exhausted**, gave up ☠️ (terminal — becomes the DLQ trigger in Day 11) |

So a failed attempt with tries left just **stays `pending`**; only exhaustion writes `failed`.

### Separation of concerns — split "deliver" from "decide"
The retry decision belongs to the **worker** (it owns the queue), not to `deliver_event`. So I refactored:

- **`deliver_event` → returns `bool`** (success). It attempts the POST, logs the attempt, and returns —
  it **no longer touches event status**.
- **The worker** owns status transitions + the retry policy:

```python
MAX_ATTEMPTS = 5

success = deliver_event(db, event)
if success:
    update_event_status(db, event.id, "delivered")
    print(f"Delivered event {event_id}")
else:
    attempts = count_delivery_attempts(db, event.id)
    if attempts < MAX_ATTEMPTS:
        enqueue_event(event.id)                       # retry — status stays 'pending'
        print(f"Event {event_id} failed (attempt {attempts}/{MAX_ATTEMPTS}), re-queued")
    else:
        update_event_status(db, event.id, "failed")   # exhausted → terminal
        print(f"Event {event_id} failed permanently after {attempts} attempts")
```

- **Counter home:** I count attempt *rows* (`count_delivery_attempts`) — no new column. The rows *are*
  the count.
- **`attempts < MAX_ATTEMPTS`:** `deliver_event` logs the attempt *before* returning, so `count` already
  includes the try that just failed → after attempt 5, `count == 5`, not `< 5` → give up. Exactly 5 tries.

### 🐛 The bug that caused an infinite loop
`count_delivery_attempts` filtered on the wrong column:

```python
db.query(DeliveryAttempt).filter(DeliveryAttempt.id == event_id)        # ❌ .id = attempt's own PK
db.query(DeliveryAttempt).filter(DeliveryAttempt.event_id == event_id)  # ✅ .event_id = the FK
```

Filtering on `.id` (the attempt's primary key) instead of `.event_id` (the foreign key) made the count
stuck at ~0 → `0 < 5` **forever** → the worker re-queued endlessly. The Day-8 test couldn't catch it:
on a *first* attempt the count genuinely is 0, so `attempt_number = 0 + 1 = 1` looked correct. Lesson:
a value that's correct at 0 tells you nothing about whether it *increments*.

> ⚠️ No backoff yet — retries fire **immediately**, so a dead endpoint burns all 5 tries in ~seconds.
> That's the stepping stone; Day 10 adds the delay.

---

## Part 9b — The reliable queue (`BLMOVE`)

### The gap (I spotted this in Day 8)
`BRPOP` **deletes** the id the instant it pops it. So the moment the worker takes an id, it exists
*nowhere but in the worker's memory*. Kill the worker mid-delivery → the id vanishes, and the event is
stranded `pending` forever. Lost.

### The fix — move, don't delete + acknowledge
```
BRPOP:   main ──pop & DELETE──► worker            (crash = id lost)
BLMOVE:  main ──atomic move──► processing ──► worker
                                   │ finish → LREM (ack)
                                   │ crash  → id STAYS in processing → recoverable
```

- **`BLMOVE` is atomic** — the id is *always* in either `delivery` or `processing`, never in limbo.
  Gotcha: unlike `BRPOP` (returns a tuple), **`BLMOVE` returns the value directly** — no unpacking:
  ```python
  event_id = client.blmove(QUEUE_KEY, PROCESSING_KEY, timeout=0, src="RIGHT", dest="LEFT")
  ```
  `src="RIGHT"` mimics the old `BRPOP` (take the oldest); `dest="LEFT"` parks it on the processing list.
- **Ack = `LREM`**, placed *after* the if/else, inside the `try` (only runs on clean completion; also
  before the `event is None` continue):
  ```python
  client.lrem(PROCESSING_KEY, 1, event_id)   # remove 1 occurrence → "done, no longer in-flight"
  ```
  **Deliberately NOT** in the `except` branch (leave a crashed id in `processing` for recovery) and NOT
  in `finally`.

### Recovery — reclaim orphans on startup
```python
def recover_orphans() -> None:
    while True:
        event_id = client.lmove(PROCESSING_KEY, QUEUE_KEY, src="LEFT", dest="LEFT")
        if event_id is None:      # processing empty → done
            break
        print(f"Recovered orphaned event {event_id} from processing")
```
Called once before the loop. **`LMOVE`, not `BLMOVE`** — recovery is a one-time drain, so we want the
*non-blocking* version that returns `None` when empty (the loop's exit condition). `BLMOVE` would block
forever.

### ✅ Verified (the real crash test)
- Temp `time.sleep(15)` after `blmove`, before the ack. Emitted an event → confirmed the id sat in
  `webhook:processing` mid-flight.
- `kill -9 $(pgrep -f app.worker)` during the sleep (hard crash — no `finally`, no ack). Id **survived**
  in `processing`.
- Removed the sleep, restarted → `Recovered orphaned event N from processing` → re-processed to a
  terminal state. Under `BRPOP` that event would've been lost.

### Things worth remembering
- **Attempt count persists in the DB**, so recovery *resumes* retries (attempt 3 after a crash at 2) —
  it doesn't reset, and the cap stays global. A crash-loop can't retry forever.
- **`KeyboardInterrupt` (Ctrl+C = SIGINT) is `BaseException`, not `Exception`** — so it's *not* swallowed
  by `except Exception`; the worker exits cleanly instead of treating Ctrl+C as a delivery error.
- **⚠️ At-least-once → possible double delivery:** if a crash lands *after* delivery but *before* the ack,
  recovery re-queues an already-delivered event. Accepted trade-off — **Day 12 (idempotency)** fixes it.
- **Single-worker assumption:** "everything in `processing` is orphaned" is only safe with one worker.
  Many workers would need per-worker processing lists or visibility timeouts.

---

## ⏭️ Next — Day 10
Exponential backoff + jitter: stop retrying *immediately*, space attempts out (1, 2, 4, 8s…) using a
delayed/scheduled queue (Redis sorted set), so we stop hammering struggling endpoints.

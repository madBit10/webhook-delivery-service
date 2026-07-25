# 📘 Day 12 — Idempotency (making at-least-once delivery safe)

> **What I built:** two guards so that delivering the same event more than once is *harmless*.
> **12a** — a terminal-status check in the worker so a re-queued, already-finished event is skipped, not
> re-delivered. **12b** — an `X-Idempotency-Key` header on the outbound POST so the *receiver* can dedupe the
> one duplicate we can never prevent locally.

---

## 🧠 The concept

**Idempotency:** an operation is idempotent if doing it many times has the same effect as doing it once.
For webhooks: delivering the same event twice should leave the receiver in the same final state as
delivering it once. We can't stop duplicates — so we make them *harmless*.

**Why duplicates are unavoidable:** exactly-once delivery is **impossible** over an unreliable network. So
the system is deliberately **at-least-once** (better to deliver twice than zero times).

> **at-least-once delivery + idempotency = exactly-once _effect_** ← the standard pattern.

---

## The two duplicate windows

Each half of Day 12 closes exactly one window.

| Window | Cause | Fix |
|--------|-------|-----|
| Re-queue a finished event | Worker crashes *after* delivering but *before* the status write → `recover_orphans()` re-queues an already-`delivered` id | **12a** sender-side guard |
| POSTed, then crashed | The request already reached the receiver, but we crashed before saving status → we retry | **12b** receiver-side key |

- **12a is a local optimization** — cheaply catches the common re-queue case.
- **12b is the real correctness guarantee** — only the receiver can dedupe the unavoidable case.

---

## Part 12a — terminal-status guard (in the worker)

Statuses split into **terminal** (`delivered`, `dead`) and **non-terminal** (`pending`). Before delivering,
check the event's current DB status; if it's already terminal it must be a re-queued duplicate → **ack and
skip**, don't re-POST.

```python
event = get_event(db, event_id)
if event is None:                                # ghost id
    client.lrem(PROCESSING_KEY, 1, event_id)
    continue
if event.status in ("delivered", "dead"):        # already done → duplicate
    client.lrem(PROCESSING_KEY, 1, event_id)     # ack, then
    print(f"Event {event_id} already {event.status}, skipping")
    continue
```

- **Placement matters:** the guard is a *sibling* of the `event is None` check, and runs *after* it — so
  `event.status` is always safe to read (never `None.status`).
- Mirrors the `None` block's shape: `lrem` ack **then** `continue`.

---

## Part 12b — `X-Idempotency-Key` header (on the outbound POST)

```python
headers = {"X-Idempotency-Key": str(event.id)}
response = httpx.post(url, json=payload, headers=headers, timeout=5.0)
```

**The one design rule that makes or breaks it:** the key must be **stable across every retry of the same
event** and **unique across different events**.

| candidate | stable across retries? | verdict |
|-----------|------------------------|---------|
| `event.id` | ✅ same for attempt 1 and attempt 5 | ✅ use this |
| `attempt_number` | ❌ changes 1,2,3,4,5 | ❌ receiver would see every retry as new |

- `str(...)` because HTTP header values must be strings (`event.id` is an `int`).

---

## Honest caveat — what's still unsolved

The sender can **never** fully guarantee no duplicates; the "POSTed then crashed before recording" window is
fundamental. Deduplication is ultimately the **receiver's** job, using the key we hand them. Our
responsibility is to send a **correct, stable key on every attempt** — which 12b does.

---

## ❓ Q&A / interview cheat-sheet

- **"How do you get exactly-once delivery?"** — You don't, over a network. You do at-least-once + make the
  operation idempotent so duplicates are harmless → exactly-once *effect*.
- **"Why key on the event id and not the attempt number?"** — The key must be constant across retries of the
  same event; attempt_number changes each retry, so it would defeat dedup entirely.
- **"Why still send a key if 12a already skips duplicates?"** — 12a only catches the *local* re-queue case.
  The POSTed-then-crashed duplicate never gets a chance to hit 12a; only the receiver, holding the key, can
  drop it.
- **"Whose responsibility is dedup?"** — The receiver's. The sender's job is a stable idempotency key.

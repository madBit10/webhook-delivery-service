# 🔀 Day 14 — Retry policy by status class

> **What I built:** the worker now decides *whether to retry at all* based on **why** delivery failed, instead
> of retrying everything identically. `deliver_event` returns a `DeliveryOutcome` enum (`DELIVERED` /
> `RETRYABLE` / `TERMINAL`) instead of a bare `bool`, and the worker branches three ways.

---

## 🗣️ Plain-language version — start here when revising

### The problem I saw live

On Day 13 I corrupted the shared secret on purpose. The receiver returned **401**. My worker then retried it
**five times over ~10 seconds** before dead-lettering it.

Every one of those retries was pointless. A wrong shared secret is wrong **forever** — waiting doesn't fix it.
Worse, the retries *delayed* the dead-letter entry, which is the thing that actually tells a human
*"your integration is broken."*

### The idea

> **Retry things that might succeed later. Don't retry things that are permanently wrong.**

- Their server crashed (500)? → try again, it might recover
- The network timed out? → try again
- They rejected the request itself (401, 400, 404)? → **stop.** It'll be rejected next time too

### What changed

`deliver_event` used to answer one question: *"did it work?"* — a yes/no.
Now it answers a second: *"and should we bother trying again?"*

So it returns one of three values instead of true/false, and the worker has three branches instead of two.

### The result

A 404 now dead-letters on the **first** attempt instead of the fifth. Same destination, five times less noise,
and the alert arrives ten seconds sooner.

---

## The classification rule

| Response | Meaning | Policy |
|---|---|---|
| `2xx` | delivered | done |
| `5xx` | their server is having a moment | **retry** |
| no response (timeout, refused, DNS) | network problem | **retry** |
| `408` Request Timeout | transient, despite being 4xx | **retry** |
| `429` Too Many Requests | rate limited | **retry** (ideally honouring `Retry-After`) |
| other `4xx` (400, 401, 403, 404, 422) | the request itself is wrong | **terminal — dead-letter now** |
| anything else (`3xx`, `1xx`) | unrecognised | **terminal — fail closed** |

**"4xx = don't retry" is the rule of thumb; `408` and `429` are the exceptions.** Getting those two right is
what separates a real implementation from a naive one.

### The 404 debate — and why the answer depends on your retry window

**Terminal:** the URL doesn't exist on their server. A typo in the registered path, or a deleted route. Won't
fix itself.

**Retryable:** 404s are common *during a deploy* — the app restarts, routes aren't registered yet, a load
balancer sends traffic to a container that isn't ready. That genuinely recovers.

Both are real. **The deciding factor is the retry window**, which means this decision is *not independent* of
Day 10's backoff design:

- My window is ~10 seconds total. A deploy takes longer than that, so retrying a 404 would burn all five
  attempts and still dead-letter before their service came back. **Terminal is correct here.**
- Production senders retry over hours or days. There, 404-as-retryable catches real recoveries, so they
  classify it the other way.

Same status code, opposite decision, because the surrounding system differs. **The comment in the code is
worth more than the decision** — it tells future-me what would change my mind.

`410 Gone` is the unambiguous one: "permanently gone", always terminal. Some systems auto-disable the endpoint
on a 410, since the receiver is explicitly saying *stop sending*.

---

## Design decision — why an Enum, not two booleans

The tempting move is `return (success, retryable)`. **Don't.** Call sites become
`if not success and retryable:` and you end up reading `(False, False)` vs `(False, True)` at a glance.

```python
class DeliveryOutcome(str, Enum):
    DELIVERED = "delivered"
    RETRYABLE = "retryable"
    TERMINAL  = "terminal"
```

| Why | |
|---|---|
| **Reads as intent** | `if outcome is DeliveryOutcome.RETRYABLE` says what it means |
| **Typo-proof** | a misspelled member is an `AttributeError` at import; a misspelled string is a silent no-match (exactly the Day 12 `"deliverd"` bug) |
| **Extends cleanly** | adding `RATE_LIMITED` for `429` later doesn't change existing branches |
| **Three states, not four** | two booleans give four combinations, one of which (`success=True, retryable=True`) is nonsense |

The `str` mixin means it also prints and serialises as a plain string — handy for logging.

**Compare with `is`, not `==`.** Enum members are singletons, so `is` is both correct and idiomatic — and it
won't silently succeed if you ever compare against a raw string by accident.

---

## Where the decision lives — observe vs decide

Same call as Day 9, when I moved status-setting out of `deliver_event`:

- **`deliver_event` OBSERVES.** It's the only thing holding the HTTP response, so it classifies.
- **The worker DECIDES.** It owns the queue, so it chooses retry vs dead-letter.

Classification is a fact about the response. Policy is a decision about the queue. Keeping them apart means the
retry strategy can change without touching the HTTP code, and vice versa.

`classify_response` is also deliberately **pure** — one `Optional[int]` in, one enum out. No `db`, no `event`,
no `response` object. That constraint means it can't grow into doing I/O, and every branch is testable with a
one-line assert.

```python
def classify_response(status_code: Optional[int]) -> DeliveryOutcome:
    if status_code is None:                        # timeout / refused / DNS
        return DeliveryOutcome.RETRYABLE
    elif 200 <= status_code < 300:
        return DeliveryOutcome.DELIVERED
    elif status_code in (408, 429):                # MUST come before the general 4xx
        return DeliveryOutcome.RETRYABLE
    elif 400 <= status_code < 500:
        # terminal for now. 404 is arguable — transient during a deploy — but our
        # retry window (~10s) is shorter than a deploy, so retrying gains nothing.
        # Revisit if the window grows to hours/days.
        return DeliveryOutcome.TERMINAL
    elif 500 <= status_code < 600:
        return DeliveryOutcome.RETRYABLE
    else:                                          # 3xx, 1xx — fail closed
        return DeliveryOutcome.TERMINAL
```

### ⚠️ Branch order is not cosmetic

The exceptions (`408`, `429`) **must** sit above the general 4xx rule. In an if-chain the first match wins and
the rest never run — put the general rule first and `429` never reaches its own branch.

**Specific cases before general ones.** Same reason the `event is None` guard sits above the terminal-status
guard in the worker.

### Why `TERMINAL` for the `else`

Terminal doesn't mean *lost* — it means **stop retrying automatically and escalate to a human**. The DLQ and
`POST /dlq/replay` (Day 11) are the recovery path.

Compare the two mistakes for an unrecognised code:
- Wrongly retryable → burns 5 attempts on something I don't understand, then dead-letters anyway
- Wrongly terminal → lands in the DLQ immediately, visible, replayable in one curl

**Fail closed.** When you hit something unanticipated, surface it rather than quietly automating around it.

*(Bonus for `3xx` specifically: httpx doesn't follow redirects by default, so a `301` arrives as a real
response. A redirect means they moved their endpoint and should update the registration — a human problem.
It's also why you **don't** want auto-follow on webhooks: a receiver could redirect you to an internal
address, which is a request-forgery vector.)*

---

## The restructure — classify ONCE, after the try/except

**The bug I hit:** I first put the classification *inside* the `try`. But `deliver_event` has three exit paths,
and the `except` branch never set `outcome` — so a timeout would have crashed with `UnboundLocalError`, and
meanwhile the function still returned the old `success` bool, making the whole change inert.

**The fix** — by the time control reaches the line below, `status_code` is set on **every** path (a number, or
`None`). One classify call covers all three exits:

```python
try:
    response = httpx.post(url, content=request_body, headers=headers, timeout=5.0)
    status_code = response.status_code
    body = response.text
except httpx.RequestError as e:
    status_code = None
    body = str(e)

# both paths have status_code by now — classify ONCE
outcome = classify_response(status_code)
success = outcome is DeliveryOutcome.DELIVERED

...
create_delivery_attempt(db, event.id, success, status_code, body, attempt_number, duration_ms)
return outcome
```

Notice the try/except is now reduced to **recording what happened**. All interpretation lives below it, in one
place. `success = False` disappeared from the `except` branch — it's *derived*, so `None → RETRYABLE →
success = False` falls out automatically. One less place for two definitions to drift apart.

**Both values are kept, because they answer different questions:**

| | Question | Consumer |
|---|---|---|
| `success` (bool) | *did it work?* | the `delivery_attempts` row — a historical fact |
| `outcome` (enum) | *what should we do now?* | the worker — a policy decision |

---

## The worker's three-way branch

```python
outcome = deliver_event(db, event)

if outcome is DeliveryOutcome.DELIVERED:
    update_event_status(db, event.id, "delivered")

elif outcome is DeliveryOutcome.RETRYABLE:
    attempts = count_delivery_attempts(db, event.id)
    if attempts < MAX_ATTEMPTS:
        delay = schedule_retry(event.id, attempts)
    else:
        dead_letter(event_id)
        update_event_status(db, event.id, "dead")
        print(f"Event {event_id} failed permanently after {attempts} attempts")

else:  # TERMINAL
    dead_letter(event_id)
    update_event_status(db, event.id, "dead")
    print(f"Event {event_id} rejected by receiver — dead-lettered without retry")
```

**The two dead-letter paths log different messages on purpose.** Both end in status `dead`, but for opposite
reasons — *"we tried five times and it never worked"* vs *"they rejected it outright, we didn't bother."* At
2am in a log, that distinction is the entire point of the day.

**The dead-letter block is duplicated, deliberately.** Two occurrences of three lines isn't a maintenance
problem, and extracting it now would hide that these are *different decisions that happen to share an action
today* — terminal failures might later auto-disable the endpoint. Extract if a third caller appears.

---

## 🐛 Bugs caught in review

**1. `sign_payload()` called with no arguments** — would have crashed loudly. The friendly kind.

**2. The timer started above the crypto.** `start = time.perf_counter()` sat above the signing work, so
`duration_ms` would have included the JSON dump and HMAC. Microseconds in practice, but it changes what the
column *means*: it's a record of how slow the **subscriber** is, and folding my own work in makes it a
measurement of us-plus-them.

**3. `body` meant two things.** `request_body` (bytes) at the top, `response.text` (str) further down. It
*worked* — signing and sending both happen before the reassignment — which is what made it worth fixing. One
edit away from silently signing `response.text`, and the type changed underneath the name. Renamed the request
side. Same family as the cross-step handoff bug from Project 3.

**4. 🚨 `deliver_event` called TWICE.** After renaming `success` → `outcome` I left the old line behind:

```python
outcome = deliver_event(db, event)      # line 50
success = deliver_event(db, event)      # line 52 ← delivers AGAIN
```

`deliver_event` isn't a getter — it performs a real POST and writes an attempt row. So **every event was
delivered twice**: the subscriber got two POSTs, and `attempt_number` climbed twice as fast, silently halving
the retry budget (MAX_ATTEMPTS hit after ~2 cycles instead of 5).

> **When you rename something mid-function, check whether the old line is *gone* or merely *superseded*.**
> A stale assignment to an unused variable is harmless. A stale **call with side effects** is not.

---

## 🧪 Testing — and the time the test lied

### First attempt: httpbin returned 503 for everything

Registered `httpbin.org/status/404`, `/status/429`, `/status/500`. All three came back **503** — httpbin is a
free public service and its own infrastructure was overloaded. It never ran the requested status.

Result: all three showed `5 attempts` (correct — 503 *is* a 5xx), and **the two branches I was actually testing
were never touched.**

> **The code was right. The test setup was wrong.** Looking only at the `attempts` column, I'd have concluded
> the classifier was broken. If httpbin had happened to return 404 once, I'd have concluded it worked.
> **Always verify what the test RECEIVED, not just what it produced.** The `code` column is what saved this.

### Fix: a local status server, no internet involved

```python
# examples/status_server.py
from fastapi import FastAPI, Response

app = FastAPI()

@app.post("/status/{code}")
def echo_status(code: int):
    return Response(status_code=code)
```

Kept **separate** from `verify_receiver.py` — that one is the reference implementation handed to subscribers;
bolting a test double onto it muddies what it's for.

### The full matrix — all six branches

| # | Branch | Trigger | Result |
|---|---|---|---|
| 1 | `2xx` → DELIVERED | `httpbin/post` | **1 attempt**, `delivered`, 200 ✅ |
| 2 | `5xx` → RETRYABLE | httpbin 503 | **5 attempts**, `dead`, 503 ✅ |
| 3 | other `4xx` → TERMINAL | `:9001/status/404` | **1 attempt**, `dead`, 404 ✅ |
| 4 | `408`/`429` → RETRYABLE | `:9001/status/429` | **5 attempts**, `dead`, 429 ✅ |
| 5 | `None` → RETRYABLE | `localhost:9999` (nothing listening) | **5 attempts**, `dead`, **NULL** ✅ |
| 6 | `else` (3xx) → TERMINAL | `:9001/status/301` | **1 attempt**, `dead`, 301 ✅ |

```sql
SELECT e.id, e.status, COUNT(da.id) AS attempts, MAX(da.response_status_code) AS code
FROM events e LEFT JOIN delivery_attempts da ON da.event_id = e.id
GROUP BY e.id, e.status ORDER BY e.id DESC LIMIT 4;
```
```
 id | status | attempts | code
 40 | dead   |        5 |          ← no response, NULL code
 39 | dead   |        1 |  301
 38 | dead   |        5 |  429     ← 4xx, RETRIED
 37 | dead   |        1 |  404     ← 4xx, TERMINAL
```

**Rows 37 and 38 are the proof.** Both 4xx, opposite outcomes — the only evidence that could distinguish
correct branch ordering from broken. Had `408/429` sat below the general 4xx rule, row 38 would read
`1 attempt` and nothing in the code would *look* wrong.

**Row 40** confirms the `None` path: connection refused → no response → `status_code = None` → `RETRYABLE`, and
the nullable column honestly records "no response" rather than inventing a zero. That path changed when
classification moved below the try/except, so it needed re-verifying.

**Tests 1 and 2 were only regression checks** — they confirm old behaviour still works. Tests 3–6 are the ones
that exercise what Day 14 actually added.

**Connection-refused beats a timeout for test 5:** it fires instantly instead of waiting out 5 seconds, and
`httpx.ConnectError` subclasses `httpx.RequestError`, so the existing `except` catches it.

---

## Still open

- **`429` doesn't honour `Retry-After`.** It's retried on the normal exponential schedule, ignoring the delay
  the receiver explicitly asked for. Proper handling = parse the header and override the backoff.
- **No `RATE_LIMITED` outcome.** The enum extends cleanly when that's worth doing.
- **Terminal failures don't disable the endpoint.** A receiver returning 401 forever will keep receiving
  events. Real systems auto-disable after sustained terminal failures and notify the owner.

---

## ❓ Q&A / interview cheat-sheet

- **"Should a webhook sender retry a 401?"** — No. 4xx means the request itself is wrong and will stay wrong;
  retrying delays the dead-letter entry that alerts a human. Retry 5xx and timeouts; treat most 4xx as terminal
  — with `408` and `429` as the exceptions.
- **"Why an enum instead of returning two booleans?"** — Two booleans give four combinations, one of which is
  meaningless, and call sites read as `(False, True)`. An enum names the three real states, is typo-proof at
  import time, and extends without changing existing branches.
- **"Where should the retry decision live?"** — With whoever owns the queue. The delivery function *observes*
  (it holds the response, so it classifies); the worker *decides* (it owns the queue, so it picks retry vs
  dead-letter). Splitting them means the retry strategy can change without touching the HTTP code.
- **"How would you test a status-code classifier?"** — Run a local server that returns arbitrary codes, and
  cover every branch — especially the exceptions. `404` and `429` are both 4xx with opposite outcomes, so
  they're the only pair that proves branch ordering is right.
- **"Your test hit a public API and got unexpected results — what did you learn?"** — That the test
  infrastructure can lie. httpbin returned 503 instead of the codes I asked for, so two branches were never
  exercised while the results *looked* plausible. Verify what the test received, not just what it produced —
  then remove the external dependency entirely.

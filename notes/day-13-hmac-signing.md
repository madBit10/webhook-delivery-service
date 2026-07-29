# 🔐 Day 13 — HMAC request signing (proving the webhook came from us)

> **What I built:** every outbound delivery now carries an HMAC-SHA256 signature over the exact bytes sent,
> keyed by the endpoint's `secret` — the column generated back on Day 3 and never used until now. Plus a
> standalone receiver in `examples/` that independently verifies it, so the scheme is proven both ways.

---

## 🗣️ Plain-language version — start here when revising

### The problem

Your service POSTs data to whatever URL a customer gave you. From **their** side, that's just a door open to
the internet. Anyone who learns the address can knock on it and pretend to be you.

*"But we use HTTPS!"* — HTTPS only means **nobody read it on the way**. It doesn't say **who sent it**. Anyone
can open an HTTPS connection to their server. So they need a way to check: *is this really from the webhook
service, and did anyone change it in transit?*

### The idea

You and the customer share a **secret password** (generated when they registered).

Before sending, you take the message + that password and run them through a one-way math function. Out comes a
64-character fingerprint, which you send along in a header. They do the same math with their copy of the
password. Same fingerprint = it's really you, and nothing was changed.

That fingerprint is the **signature**. The function is **HMAC**.

Why it works: without the password you can't produce the right fingerprint, and changing even one character of
the message produces a completely different one.

### What you built

**1. `app/core/security.py`** — makes the fingerprint.

```
take: secret, timestamp, body
glue: "timestamp." + body
run:  HMAC-SHA256 with the secret
give: a 64-character hex string
```

**2. `app/services/event.py`** — the sending side. Before every POST it now converts the payload to bytes
**once**, makes the fingerprint from those bytes, adds the signature/timestamp/content-type headers, and sends
**those exact bytes**.

**3. `examples/verify_receiver.py`** — a pretend customer that checks the fingerprint, to prove it works.

### The three traps

**Trap 1 — sign the same bytes you send.** Before, `httpx` converted your data to text itself, behind the
scenes. Make the fingerprint from *your* version while httpx sends *its* version and the two differ slightly
(spacing, key order) — so the fingerprint never matches. Nothing errors; it just always fails. → Convert once
yourself and hand over those exact bytes (`content=` instead of `json=`).

**Trap 2 — the dot matters.** You glue the timestamp and body together. With nothing between them, `12`+`3abc`
and `123`+`abc` become the same glued text, so one signature would work for two different messages. The `.`
marks where one ends and the other begins.

**Trap 3 — a signature is valid forever.** Someone who records one of your messages could resend it a thousand
times later and it would still pass. So you also send the **time** it was signed, and the customer rejects
anything older than 5 minutes. The time sits *inside* the fingerprint, so nobody can fake it.

### On the receiving side

Four checks, in order:

1. Are the headers even there?
2. Is the timestamp a real number?
3. Is it recent (within 5 minutes)?
4. Does the fingerprint match?

Any failure → **401**, with the same message every time, so an attacker can't learn *which* check they failed.

Two rules: read the **raw** body, not the parsed version (same reason as Trap 1); and compare with
`compare_digest`, not `==` — normal comparison stops early at the first wrong character, and that tiny speed
difference leaks the answer bit by bit.

### The clever bit about testing

Your receiver **doesn't reuse your signing function** — it redoes the math itself. If both sides used the same
function, a mistake in it would cancel out and the test would pass anyway. Two independent implementations
agreeing is real proof; one implementation agreeing with itself proves nothing.

And you ran **two** tests: correct password → accepted ✅, one character changed → rejected ✅. **The second one
is the important one** — otherwise you'd only know it can say yes, not that it can say no.

### What you learned by accident

When the tampered event failed, the worker retried it 5 times before giving up. But a wrong password is wrong
*forever* — retrying is pointless and just delays the alert. Real systems split it: server error/timeout →
**retry** (might recover); rejected request (401, 400) → **stop immediately** (won't fix itself).

### If you remember five things

1. HTTPS hides the message; a signature proves **who sent it**.
2. **Sign the exact bytes you send** — convert once.
3. Put a **separator** between glued fields.
4. Add a **timestamp**, or old messages can be replayed forever.
5. Test with a **separate** implementation, and always test that it can say **no**.

---

## 🧠 The concept — why sign at all

The delivery is a plain `POST` to a URL the subscriber gave us. From **their** side, `POST /webhooks` sits
open on the public internet. Two problems:

| Problem | Question the receiver can't answer |
|---|---|
| **Authenticity** | "Did this actually come from the webhook service, or from anyone who learned my URL?" |
| **Integrity** | "Was the body modified between them and me?" |

**HTTPS does not solve #1.** TLS proves the *channel* is secure — it says nothing about *who wrote the
message*. Any random client can open a perfectly valid TLS connection to their server and post junk.

**The fix:** we share a secret with them (handed over once at registration). We compute a keyed hash over the
bytes we send and put it in a header. They recompute it with their copy of the secret. Match = the message
came from someone holding the secret, and the body is byte-identical to what was signed.

```
signature = HMAC-SHA256(key = endpoint.secret, message = timestamp + "." + raw_body)
```

---

## Why HMAC and not `sha256(secret + body)`

The naive construction is **broken**, not just unfashionable. SHA-256 is a Merkle–Damgård hash: its internal
state after hashing a message *is* the output. So an attacker holding `hash(secret + body)` can resume from
that state and compute `hash(secret + body + extra)` — a valid digest for a message they extended —
**without ever knowing the secret**. That's a **length-extension attack**.

HMAC's double-hash-with-padded-key construction (`H(key ⊕ opad ‖ H(key ⊕ ipad ‖ msg))`) exists specifically
to close it. Rule: **never roll your own MAC; use `hmac`.**

---

## Part 1 — the signing helper (`app/core/security.py`)

```python
import hmac
import hashlib

def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    msg = f"{timestamp}.".encode() + body
    mac = hmac.new(secret.encode(), msg, hashlib.sha256)
    return mac.hexdigest()
```

Three things this file gets right on purpose:

- **Keys and messages must be `bytes`** — hence `.encode()` on both. `hmac.new` won't take a `str`.
- **`hmac.new()` returns an HMAC *object*, not the signature.** You have to ask it: `.hexdigest()` for a hex
  string (64 chars for SHA-256 — 32 bytes × 2), `.digest()` for raw bytes. **Hex, because it's going into an
  HTTP header** and headers must be printable ASCII. It's also what Stripe/GitHub/Shopify use, so it's what a
  subscriber expects.
- **It touches no `db` and no `event`.** Pure function → lives in `app/core/` next to `config.py`, testable in
  isolation, importable by anything without dragging in DB or HTTP dependencies.

### ⚠️ The `.` separator is load-bearing

Without a delimiter the timestamp and body run together into one undifferentiated blob:

```
timestamp=12,  body=b'3abc'  →  b'123abc'
timestamp=123, body=b'abc'   →  b'123abc'   ← IDENTICAL
```

Two genuinely different requests produce the **same signed message**, so one signature authenticates both —
an attacker can shave a digit off the timestamp, prepend it to the body, and the signature still verifies.
This is **boundary ambiguity**, and a one-character delimiter closes it. It's why Stripe's scheme is literally
`{timestamp}.{payload}`.

---

## Part 2 — signing the delivery (`app/services/event.py`)

```python
request_body = json.dumps(payload).encode()          # serialize ONCE
timestamp = int(time.time())
signature = sign_payload(endpoint.secret, timestamp, request_body)

start = time.perf_counter()                          # timer starts AFTER the crypto

try:
    headers = {
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(event.id),
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Signature": signature,
    }
    response = httpx.post(url, content=request_body, headers=headers, timeout=5.0)
```

### 🚨 THE bug this design avoids: serialize once

Before today the call was `httpx.post(url, json=payload, ...)` — **httpx** serialized the dict internally, at
send time, and we never saw those bytes. That's fine when you're not signing. The moment you sign, it becomes
**the #1 webhook integration bug in the wild**:

> Sign one byte string, let httpx send a *different* one (different key order, different whitespace), and the
> subscriber's verification fails every single time. It looks like broken crypto. It's a byte mismatch.

**JSON is not canonical** — the same dict has many valid serializations. So:

> **Serialize once → sign those bytes → send those exact bytes.**

That's why `content=request_body` replaced `json=payload`.

⚠️ **Switching to `content=` means httpx stops setting `Content-Type` for you.** With `json=` it added
`application/json` automatically; with raw bytes it has no idea what you're sending. Set it manually or the
receiver may refuse to parse.

### Two clocks, two jobs

| | `time.time()` | `time.perf_counter()` |
|---|---|---|
| Measures | wall clock, seconds since 1970 | ticks from an arbitrary origin |
| Meaningful across machines? | ✅ yes | ❌ no |
| Can jump backwards? | ✅ yes (NTP, DST) | ❌ never — monotonic |
| Used for | the signature **timestamp** | `duration_ms` on the attempt row |

Neither can do the other's job. `perf_counter()` might return `48213.7` — meaningless to a receiver comparing
against their own clock. And `time.time()` can be shifted backwards mid-request by an NTP sync, which is
exactly why you never measure durations with it (you'd occasionally record a negative duration).

**`start` moved below the signing** so `duration_ms` measures only the network round-trip. The HMAC takes
microseconds so the number barely moves — but the column's *meaning* matters: it's a record of how slow the
**subscriber** is, and folding our own work into it makes it a measurement of us-plus-them.

### 🐛 Bug caught in review: `body` meant two things

```python
request_body = json.dumps(payload).encode()   # request body — bytes
...
body = response.text                          # response body — str
body = body[:1000]                            # ...which one?
```

It **worked** — signing and sending both happen before the reassignment, so the sequence was safe. That's
what made it worth fixing: it's not a bug you'd catch in testing, it's one edit away from becoming one. Add a
retry inside the function later and you'd sign `response.text`. The signature would compute fine, send fine,
and never verify — the hardest class of bug to trace. The type silently changes too (`bytes` → `str`).

Same family as the **cross-step variable handoff** bug from Project 3. Fix: rename the request side to
`request_body`, leave `body` meaning the response body everywhere downstream.

---

## Part 3 — the receiver (`examples/verify_receiver.py`)

```python
@app.post("/webhook")
async def receive(request: Request):
    raw = await request.body()                    # ← bytes, NOT request.json()
    ts  = request.headers.get("X-Webhook-Timestamp")
    sig = request.headers.get("X-Webhook-Signature")

    # 1. headers present?
    if ts is None or sig is None:
        raise HTTPException(status_code=401, detail="invalid signature")

    # 2. parseable? (STILL UNTRUSTED at this point)
    try:
        ts_int = int(ts)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid signature")

    # 3. fresh? (abs → catches future timestamps too)
    if abs(time.time() - ts_int) > 300:
        raise HTTPException(status_code=401, detail="invalid signature")

    # 4. signature matches?
    expected = hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="invalid signature")

    return {"received": True}
```

### Read the RAW bytes — the mirror of the send-side trap

`await request.body()`, **never** `await request.json()`. Parse the JSON and re-serialize it and you get
different bytes → the hash won't match. **Sign what was sent; verify what was received.** Never a
round-tripped version of it.

(This is why the handler is `async def` — `request.body()` is a coroutine. It's also the first time this
project has used FastAPI's `Request` object instead of letting a Pydantic model parse the body.)

### `hmac.compare_digest`, never `==`

String `==` short-circuits at the first differing byte, so it returns **faster** for a closer guess. That
timing difference leaks the signature one byte at a time. `compare_digest` is constant-time.

### Order of checks: cheap first, and everything before step 4 is hostile

```
1. headers missing?   → 401
2. ts not a number?   → 401
3. ts too old?        → 401
4. signature bad?     → 401
5. pass               → 200
```

The timestamp check is an integer subtraction; the signature check hashes the whole body. Cheap test first
means garbage gets rejected without burning a hash. *(Judgment call, not correctness — Stripe's library checks
the signature first. What isn't optional is that **both** happen.)*

> **The key insight: at steps 1–3 you are parsing data you have not yet authenticated.** `int("abc")` raises
> `ValueError` → a 500 instead of a clean rejection. Every step before the signature check must survive
> whatever an attacker sends. *After* step 4, the timestamp is trustworthy — because it was inside the signed
> message. That's the whole reason it's in there.

### Identical `detail` on every rejection

Only the `print` differs. The caller learns "no", never "no, your timestamp was stale but your signature was
fine" — that second answer is a free probe into the validation logic.

### Why the timestamp exists at all

A signature proves authenticity **forever**. Anyone who captures one valid request can replay it tomorrow, a
thousand times, each copy with a perfectly valid signature. Binding a timestamp *into the signed message* and
sending it alongside lets the receiver reject anything outside a ~5 minute window. `abs()` matters — a
timestamp far in the *future* is equally suspect, and a one-sided `now - ts > 300` check would let
`9999999999` sail through forever.

---

## 🧪 The testing principle — don't import your own function

The receiver **deliberately does not** `from app.core.security import sign_payload`. It reimplements the hash
by hand.

> If both sides call the same function, a bug cancels out. Forget the `.` separator on **both** ends and the
> signatures still match perfectly — a green check that proves nothing.

An independent reimplementation is what makes it a test rather than a tautology. It's also honest to
production: a real subscriber has your *docs*, not your codebase. Which means `examples/verify_receiver.py`
doubles as the reference snippet you'd hand them.

**Two tests, and the second is the one that matters:**

| Test | Setup | Result |
|---|---|---|
| **Valid** | correct secret | receiver printed `VALID: {"amount": 100}`, event → `delivered` ✅ |
| **Tampered** | one character of `SECRET` changed | `REJECTED: signature mismatch`, 401, event → retries → `dead` ✅ |

Test 1 alone would not have proven the verification *discriminates*. Plenty of receivers "verify" and would
accept anything.

⚠️ **Gotcha hit during setup:** every endpoint gets its **own** `secrets.token_hex(32)`. Pasting the httpbin
endpoint's secret while testing against the `localhost:9000` endpoint gives `signature mismatch` — which looks
like a crypto bug and is actually the wrong key.

---

## What test 2 accidentally revealed — 4xx vs 5xx

Watching the tampered event retry 5 times with backoff and land in the DLQ confirmed Days 9–11 still work
under a brand-new failure mode, without touching any of that code.

But it exposed a real gap: **a 401 is a permanent failure.** A wrong shared secret will still be wrong in 60
seconds. Retrying it 5 times burns attempts and *delays* the DLQ entry that would actually alert someone.

Production senders split the two:

| Response | Meaning | Policy |
|---|---|---|
| `5xx`, timeout, connection error | transient — their server is having a moment | **retry with backoff** |
| most `4xx` (401, 403, 400, 422) | terminal — the request itself is wrong | **don't retry, DLQ immediately** |
| `429` | rate limited | retry, ideally honouring `Retry-After` |

Currently `deliver_event` returns a bare `bool` and the worker treats every non-2xx identically. → **Day 14
candidate.**

---

## Still open

- **`SECRET` is hardcoded** in `examples/verify_receiver.py` — fine for a local test file, but it's a reminder
  that "encrypt the endpoint secret at rest" (Fernet / secrets manager) is still open from Day 6.
- **No secret rotation** — no way to roll a compromised secret without breaking deliveries. Real systems
  support two active secrets during a rollover window (which is exactly what Stripe's `v1=` scheme prefix is
  for).
- **Rest of Phase 8:** API-key auth on our own endpoints, rate limiting.

---

## ❓ Q&A / interview cheat-sheet

- **"You're using HTTPS — why sign at all?"** — TLS secures the *channel*, not the *sender*. Anyone who learns
  the URL can open a valid TLS connection and post whatever they like. The signature proves the message came
  from someone holding the shared secret.
- **"Why HMAC instead of hashing the secret and body together?"** — `sha256(secret + body)` is vulnerable to
  length-extension: an attacker with the digest can compute a valid digest for an *extended* message without
  knowing the secret. HMAC's construction is designed to prevent that.
- **"What's the most common bug in webhook signing?"** — Signing a serialization that differs from what was
  actually sent. JSON isn't canonical, so if the HTTP library re-serializes the object, the bytes differ and
  the signature never verifies. Serialize once, sign those bytes, send those bytes.
- **"Why is there a timestamp in the signature?"** — Replay protection. A signature alone is valid forever, so
  a captured request could be replayed indefinitely. The timestamp is inside the signed message (so it can't
  be edited) and the receiver rejects anything outside a short window.
- **"Why `compare_digest` and not `==`?"** — `==` short-circuits on the first mismatching byte, leaking timing
  information that lets an attacker recover the signature byte by byte. `compare_digest` is constant-time.
- **"How do you test signing?"** — Write the verifier as an *independent* implementation, not by importing the
  signing function. Shared code means shared bugs cancel out and you get a false pass. Then test both a valid
  request *and* a tampered one — a passing verification proves nothing on its own.
- **"Should a webhook sender retry a 401?"** — No. 4xx means the request itself is wrong and will stay wrong;
  retrying delays the dead-letter entry that would alert a human. Retry 5xx and timeouts; treat most 4xx as
  terminal.

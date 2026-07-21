# 📘 Frontend Part 1 — Emit-and-Watch (the first full-stack loop)

> **What I built:** the first page of a Next.js dashboard for the webhook service. A form emits an event
> (`POST /events`) and then **polls** `GET /events/{id}` live, showing the status badge move
> `pending ⏳ → delivered / dead` in real time. This is the first time the whole stack runs together:
> React form → FastAPI → Redis queue → worker → back to the UI.

---

## 🧭 Why a frontend at all
The backend was only demo-able through Swagger/curl. A dashboard makes all the resilience work
**visible** — you can watch an event get delivered, retried, or dead-lettered. Big portfolio win: turns
"an API that returns JSON" into "a system you can see running."

**Scope locked = lean 3-page MVP** (frontend is a thin demo layer; backend stays the priority):
1. Emit + watch (this)  2. Events list  3. DLQ view + Replay. Skip the endpoints page (register via
Swagger during a demo).

## 🏗️ Setup
- `npx create-next-app@latest frontend` **inside the existing repo** (monorepo: `frontend/` beside the
  backend at root — Option A, no restructure).
- Stack: **Next.js App Router + TypeScript + Tailwind**. Checked for a nested `frontend/.git` (would make
  the main repo ignore it) — none.
- **CORS** enabled on the backend (`CORSMiddleware`, `allow_origins=["http://localhost:3000"]`) so the
  browser app (:3000) may call the API (:8000) — different port = different origin = blocked without it.
  Bug caught: typo `localhose` → `localhost` (curl doesn't hit CORS, only a browser does).
- Backend reads built to support this: `GET /events` (`response_model=list[EventRead]`) + `GET
  /events/{id}`.

## 🧩 New React/Next concepts

### `"use client"`
App Router components are **Server Components by default** (render on server, no state/handlers). Anything
interactive — `useState`, `onClick`, form inputs — needs `"use client"` as the **very first line**.
🐛 Bug hit: typo'd it `"use clinet"` → silently ignored → "useState in a Server Component" error.

### Controlled inputs (`useState`)
Each field's value lives in state: `value={x}` + `onChange={e => setX(e.target.value)}`. React is the
single source of truth for the input's contents.

### `handleSubmit` + `e.preventDefault()`
A form submit reloads the page by default; `preventDefault()` stops that so we handle it in JS and
`fetch` instead.

### `fetch`
`method`, `headers` (`Content-Type: application/json`), `body: JSON.stringify(...)`. Note `res.ok` +
manual status checks — **fetch does NOT throw on 4xx/5xx** (unlike axios). `Number(endpointId)` because
number inputs still yield a *string*.

### `useEffect` — the polling (the "watch")
Delivery is async, so after emitting, the UI has no idea when it finishes → **poll**:
```tsx
useEffect(() => {
  if (!result || result.status !== "pending") return;   // nothing to watch
  const id = result.id;
  const interval = setInterval(async () => {
    const res = await fetch(`http://localhost:8000/events/${id}`);
    if (res.ok) setResult(await res.json());             // update state → re-render badge
  }, 1500);
  return () => clearInterval(interval);                  // cleanup: stop the timer
}, [result?.id, result?.status]);
```
- **Dependency array** `[result?.id, result?.status]` — effect re-runs when these change. On `delivered`/
  `dead` the guard returns early → polling self-terminates.
- **Cleanup function** (`return () => clearInterval`) — CRITICAL. Runs before the effect re-runs and on
  unmount. Without it you leak/stack timers. Rule: *if an effect starts something, it must clean it up.*

## 🐛 Bugs caught (great real-world full-stack gotchas)
1. **`"use clinet"` typo** → the directive is a magic string; a typo silently does nothing.
2. **camelCase vs snake_case** — sent JSON keys `endpointId`/`eventType`; the API's `EventCreate` wants
   `endpoint_id`/`event_type` → 422 "Field required". **The JSON body keys must match the SERVER's
   naming, not JS convention.** (FastAPI's 422 `input` field showed exactly what was sent — invaluable.)
   Also typed `order.created` *with quotes* in the field → string literally contained quotes.
3. **`useEffect` placed inside `handleSubmit`** → "Invalid hook call." **Rules of Hooks:** hooks must be
   at the **top level of the component**, never inside event handlers / other functions / conditionals /
   loops (React tracks them by call order each render). Event handler = runs on click; hook = runs as
   part of render — different homes.
4. **Status stuck `pending` + endless polling** — not a bug: the **worker wasn't running**. The worker is
   what moves an event out of `pending`; without it the poll waits forever (~1 request/1.5s). Started
   `python -m app.worker` → status flipped, polling stopped.
5. **Port 8000 "address already in use"** — a stale `uvicorn --reload` lingering. `lsof -ti :8000 | xargs
   kill -9`.

## ✅ Verified
Emit to the 500 endpoint → badge went `pending ⏳ → dead` (red) live after the retry/backoff cycle. Full
loop proven: React → FastAPI → Redis → worker → UI, updating in real time.

## 🔭 Tooling awareness (interview-relevant)
- **fetch** (used) — built-in, zero deps, but verbose + no throw on bad status.
- **axios** — nicer ergonomics (auto-JSON, throws on 4xx/5xx, interceptors); one-line swap, adds a dep.
- **TanStack Query / SWR** — the *modern* way to fetch server data: caching, loading/error states, and
  built-in polling via `refetchInterval` (our whole `useEffect`+`setInterval` becomes one option). Chose
  raw `fetch` for the MVP to learn the mechanics; know these exist for real apps.

## 🧱 Refactor plan (deferred until needed)
Everything is in one `page.tsx` — correct at this size. Extract when there's real reuse: the **StatusBadge**
(reused on the events list page) → `components/StatusBadge.tsx`, and the repeated API base URL →
`lib/api.ts`. Will do this at the start of page 2 (events list), driven by actual reuse, not guessing.

## ⏭️ Next
Page 2 — events list (`GET /events` → table + status badges), doing the first component extraction.
(Backend still owes: **Day 12 idempotency** — paused mid-way — then Phase 8 security.)

# ⚙️ Day 17 — Continuous Integration (GitHub Actions)

> **What I built:** a GitHub Actions workflow that, on every push and every PR, boots a clean Ubuntu VM,
> installs Python 3.12 and my dependencies from scratch, starts a real Postgres 16 beside it, and runs the
> 24 tests from Day 16. Then a repository **ruleset** that makes a red run physically block a merge into
> `main`.

---

## 🗣️ Plain-language version — start here when revising

**Every time I push, a brand-new intern shows up.**

They follow instructions perfectly, remember nothing from yesterday, and are holding a laptop straight out of
the box. No Python. No Postgres. No Redis. No `.env`. They have never heard of this project.

I get to leave them **exactly one note**, written in advance. They follow it top to bottom and report back
pass or fail.

> **That note is `.github/workflows/ci.yml`.**

That's the whole concept. Everything below is a consequence of it.

### Why bother

Two reasons, and the second is the one I underestimated:

1. **Regressions get caught before merge.** Obvious.
2. **My setup becomes reproducible.** A clean runner has no `.env`, no `venv/`, no Postgres I started three
   weeks ago and forgot about. Anything my tests silently depend on has to become *explicit* or CI fails.

So CI is a permanent, brutally honest answer to **"can a stranger run this repo?"** — re-asked on every push.

---

## The vocabulary (4 nouns)

| Term | Meaning |
|---|---|
| **event** (`on:`) | what triggers a run — `push`, `pull_request`, `schedule` |
| **job** | a unit that gets its **own fresh VM** (`runs-on:`). Jobs run in parallel by default |
| **step** | one thing in a job — either `run:` (shell) or `uses:` (a prebuilt action) |
| **action** | someone else's reusable step, e.g. `actions/checkout@v4` |

Jobs are isolated VMs — nothing carries between them unless passed explicitly. Steps *inside* one job share a
filesystem and working directory.

---

## Every YAML key is a question the intern would have asked

I don't memorize this file. I **derive** it, by walking a stranger through the project on a bare machine.

| The intern says… | I answer… | The YAML |
|---|---|---|
| "When should I show up?" | every push and PR | `on: push` / `pull_request` |
| "What laptop am I on?" | a clean Ubuntu box | `runs-on: ubuntu-latest` |
| "Where is the code?" | clone the repo | `uses: actions/checkout@v4` |
| "There's no Python here." | install 3.12 | `uses: actions/setup-python@v5` |
| "What libraries?" | the pinned list | `run: pip install -r requirements.txt` |
| "It wants a database. There isn't one." | boot a Postgres beside you | `services: postgres:` |
| "It wants a `.env`. There isn't one." | here are the values | `env:` |
| "What am I actually checking?" | run the tests | `run: pytest -q` |

`checkout → setup-python → pip install → pytest` is **literally what I did by hand** when I set this project
up on my Mac months ago and forgot.

> **A workflow file is my bash history, written down, run by someone with amnesia.**

If I can set the project up on a friend's laptop, I can write a CI file. Only the notation is new.

---

## The amnesia rule explains all the weird parts

- **Why is each job a fresh VM?** Different job = different intern. They never met. Nothing built in job A
  exists in job B unless handed over explicitly (artifacts, outputs).
- **Why is caching opt-in and awkward?** Amnesia is the default *and that's the point*. A cache is sneaking a
  bag of pre-downloaded wheels onto their desk — convenient, but every cache is a small hole in the
  guarantee. So: cache `~/.cache/pip` (rebuildable), never source or a database.
- **Why can't I just commit `.env`?** The intern is a stranger — and so is anyone reading a public repo.
- **Why does CI fail when my laptop passes?** Because *I* am not amnesiac. My machine is caked in months of
  undocumented setup. **CI failing on green-locally is not CI being broken — that is the entire product.**

---

## 🔑 The big lesson: CI fails one layer at a time, outermost first

I had two known gaps (no config, no database) and pushed a deliberately minimal workflow to see what happened.
I could not have hit the database error even if I'd tried — **the import chain forces the order**:

```
conftest.py:7  →  app.db.database  →  app.core.config  →  Settings()  💥
```

`Settings()` runs at **module import time**. Nothing below it ever executes.

### Layer 1 — config (push #1)

```
E   pydantic_core._pydantic_core.ValidationError: 2 validation errors for Settings
E   database_url
E     Field required [type=missing, input_value={}, input_type=dict]
E   redis_url
E     Field required [type=missing, input_value={}, input_type=dict]
```

`.env` is gitignored, so it was never in the repo. Note **`input_value={}`** — pydantic saw a *completely
empty* environment. Not a typo, not a stale value. That's the empty laptop, printed.

**It was a collection error, not 24 test failures.** pytest never built a test list, so there were no tests to
fail.

**Fix:** a job-level `env:` block.

### Layer 2 — a stray file (push #2)

Config cleared, so the code got far enough to open a socket — and nothing was listening:

```
ERROR collecting test_db.py
test_db.py:4: in <module>
    with engine.connect() as conn:
E   psycopg2.OperationalError: connection to server at "localhost" (::1), port 5432 failed: Connection refused
!!!! Interrupted: 1 error during collection !!!!    (exit code 2)
```

The failure came from somewhere I wasn't looking: **`test_db.py` in the repo root**, a six-line smoke script
from Day 2 that opens a connection **at module level**. It has no `def test_*` — but it's *named* `test_db.py`,
so pytest collects it, and collecting means **importing**, which ran the connection. One collection error and
pytest aborts the whole session.

On my Mac it's invisible: Postgres is always up, so it silently prints `1`. **CI was the first environment
that ever asked whether that file should exist.**

> 🔒 **Rule: `test_*.py` at any level is a promise to pytest. Never park a script under that name.**

**Fix:** `git rm test_db.py` + add the Postgres service.

### Layer 3 — none. Green.

```
24 passed
```

| Push | Failure | The rule it taught |
|---|---|---|
| 1 | `ValidationError`, `input_value={}` | config is needed at **import**; nothing downstream runs |
| 2 | `connection refused` from `test_db.py` | `test_*.py` is a promise to pytest |
| 3 | — | a service is ready only when its **health check** says so |

**Fix one layer, push, read the next error.** The machine walks me down my own dependency stack for free.

---

## 🐛 The four YAML mistakes I made (all first-timer classics)

| What I wrote | Why it breaks |
|---|---|
| `-uses: actions/checkout@v4` | no space after the dash → a **key named `-uses`**, not a list item, so `steps` becomes a map |
| `-used: actions/setup-python@v5` | typo; unknown keys are hard errors, not ignored |
| `runs-on:` / `steps:` level with `test:` | makes them **siblings** of the job id → GitHub reads *three jobs* |
| `with:` aligned to the dash | must sit *inside* the list item, aligned with `uses:` |

### Two rules fix all four

```
jobs:
··test:                                  ← job id
····runs-on:·ubuntu-latest               ← belongs to the job
····steps:
······-·uses:·actions/checkout@v4        ← item 1  (dash, SPACE, key)
······-·uses:·actions/setup-python@v5
········with:                            ← aligns with `uses`, NOT the dash
··········python-version:·"3.12"
```

1. **Deeper = belongs to.** Indentation is the *only* thing expressing ownership in YAML.
2. **The dash is two columns wide.** Everything in a list item lines up with the key after `- `.

### Other YAML notes

- `python-version: "3.12"` is **quoted** — unquoted, `3.10` parses as the number `3.1`.
- `>-` is a **folded scalar**: newlines become spaces, so long option strings stay readable on multiple lines
  but arrive as one string. `|` keeps the newlines.
- `key:\n  value` (value on the next line, indented) is identical to `key: value` — a plain multi-line scalar.
  It only works because my URLs contain no `": "` sequence; a plain scalar ends at colon-space. Inline is safer.
- **`on:` parses as the boolean `true`** in YAML 1.1. I confirmed this by parsing my own file with PyYAML —
  the top-level key came back as `"true"`. GitHub special-cases it, so it works.

> **Habit worth keeping:** parse the file before pushing (`python3 -c "import yaml; yaml.safe_load(open(...))"`),
> or use VS Code + the Red Hat YAML extension, which knows the Actions schema.

---

## Service containers

```yaml
services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: exampledb_test
    ports:
      - 5432:5432
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
```

**A service container is `docker-compose.yml` scoped to one job.** Same images, same networking rules.

- **`ports: - 5432:5432`** — my *steps* run on the VM, the *database* runs in a container. Publishing the port
  is what makes it reachable at `localhost:5432`, matching my `env:` URLs. (If the **job itself** ran in a
  container, I'd drop the mapping and use `postgres` as the hostname instead.)
- **`POSTGRES_DB: exampledb_test`** — the official image creates exactly one database at startup. I made it the
  one `TEST_DATABASE_URL` names. Plain `exampledb` does **not** exist on the runner, and that's fine: nothing
  connects with it once `test_db.py` is gone.
- **`image: postgres:16`** deliberately matches my `docker-compose.yml`. Same database, same major version,
  dev and CI.

### ⚠️ The health check is the cure for the archetypal flaky-CI bug

Actions considers a container "started" the moment Docker launches it — but **Postgres needs a second or two
before it accepts connections**. Without `--health-cmd pg_isready`, my steps *race* the database:

- green usually,
- `Connection refused` occasionally,
- **and it passes when you re-run it** — which is what makes it so poisonous to debug.

With the health check, Actions blocks until `pg_isready` succeeds. In the log it shows up as an
`Initialize containers` phase before step 1.

---

## Secrets: the test isn't "does it look like a password"

My `env:` block has `postgresql://postgres:postgres@localhost:5432/...` sitting in plaintext in a committed
file. **That is correct, not sloppy.**

> **Does this grant access to anything that still exists tomorrow?**

That credential names a container that lives ~40 seconds, on a VM with no inbound internet, destroyed when the
job ends. It protects nothing. The moment a URL points at something that **outlives the job** — a staging DB,
an API key — it moves to `${{ secrets.X }}`.

`REDIS_URL` is in there purely to satisfy `Settings`. Nothing connects to it — and that's a distinction worth
naming: **a required config value ≠ a required running service.** No Redis container is needed because
`redis.from_url` is lazy, `main.py` has no lifespan hook, and no test hits `POST /events`. The day I write that
test, I add the service.

---

## ✅ How I verified it

Run history, straight from the API — the three-layer climb preserved:

```
main         push          success  e6438f8  Merge pull request #26
feat/tests   pull_request  success  7594972
feat/tests   push          success  7594972  ci: add postgres service and test env vars
feat/tests   push          FAILURE  1e1d413  added the env block to the ci
feat/tests   push          FAILURE  88177d8  ci: minimal github actions workflow
```

The run that matters is **`push` on `main`** — a merge commit is a *different commit* from the branch head, so
`main` needs its own green tick.

### Where the 42 seconds go

```
20s  Initialize containers      ← Postgres pull + pg_isready polling
 7s  pip install
 2s  pytest -q
 1s  checkout
```

**The database is half the job. My actual tests are 2 seconds.** That's the honest shape of most CI:
environment setup dominates. Which is why `cache: pip` is worth adding but won't transform anything — 7s of 42.
The 20s is the real target.

### Incidental wins CI handed me for free

- **Dependencies install clean on Python 3.12** — my Dockerfile's version. My local venv is 3.9, so this had
  literally never been tested.
- **My timestamps are cross-machine safe.** The classic version of this bug: my Mac is UTC−4, runners are UTC,
  and a "2 hours ago" comparison shifts by four hours and fails only in CI. I'm immune because of
  `DateTime(timezone=True)` (timestamptz) + aware `datetime.now(timezone.utc)`. Postgres normalizes timestamptz
  to UTC and compares absolute instants, and both sides carry an offset — **nothing in the chain asks what
  timezone the machine is in**, so the answer can't change the result. Naive datetimes + plain `DateTime` is
  where this bug lives.

---

## 🔒 Making it a gate (and the bug I nearly shipped)

A red ✗ I can merge past is a *suggestion*. Repository **ruleset** on `main`:

| Rule | What it stops |
|---|---|
| `required_status_checks: ['test']` | merging while CI is red |
| `pull_request` (approvals **0**) | pushing straight to `main` and bypassing the gate entirely |
| `non_fast_forward` | force pushes rewriting history |
| `deletion` | deleting `main` |

**Why block force pushes matters here:** `git push --force` doesn't add a commit, it *replaces* history — the
overwritten commits become unreachable and get GC'd. And it interacts with CI directly: **a green check is a
claim about a specific commit.** Force-push and `main` no longer points at that commit, so the tick now
certifies history that doesn't exist. (Feature branches are unaffected — rebase them freely.)

### 🐛 The catch: all four rules were configured, and none of them were on

GitHub's **new rulesets UI splits "what the rules are" from "what they apply to"** — unlike classic branch
protection, where the branch pattern was the first field. So I configured every rule, saved, and got:

> *"This ruleset does not target any resources and will not be applied"*

That is not a warning you can save past. It's literal:

```json
conditions: {"ref_name": {"exclude": [], "include": []}}     ← targets NOTHING
```

`main` was fully unprotected while looking completely configured. **Fix:** Targets → Add target →
**Include default branch** (the `~DEFAULT_BRANCH` token, *not* a hardcoded `main` — a literal pattern silently
stops protecting anything the day the branch is renamed, and nothing warns you).

**How to check properly** — and a real trap:

```bash
# ✅ this is the endpoint that knows about rulesets
curl -s https://api.github.com/repos/OWNER/REPO/rules/branches/main
# want 4 rule objects back; [] means dormant

# ❌ this reports "protected": false even with an ACTIVE ruleset —
#    that field only reflects CLASSIC branch protection
curl -s https://api.github.com/repos/OWNER/REPO/branches/main
```

> **This is the third time this project has bitten me the same way** — the httpbin 503 that made my Day-14 test
> lie, `print(q)` to see the real query, and now a dormant ruleset.
> **Verify what the system DID, not what I configured.**

---

## 📛 The name — what CI actually means

"Continuous Integration" does **not** mean "my tests run in the cloud."

It's from Kent Beck and Extreme Programming, and it means **integrate into the main branch continuously** —
daily, not monthly. The problem it solves is *merge hell*: six people on six branches for three weeks produce a
merge that costs more than the features did.

So why the tests? **They're what make merging that often survivable.** Merge ten times a day with no safety net
and you break main ten times a day. The suite is the mechanism; frequent integration is the goal.

> **If your branch lives three weeks, you don't have CI. You have a very expensive test runner.**

I'd been doing the *integration* half by hand for 16 days (`feat/hmac-signing`, `feat/retry-policy`,
`chore/schema-constraints` — small PRs, merged fast). Day 17 added the *automation* half.

---

## ⚠️ Known limits / next

- **No lint step.** `ruff` would catch the dead imports and unused `except Exception as e` variables I keep
  writing.
- **No coverage gate.** 24 tests is a floor, not a guarantee.
- **`Initialize containers` is 20s of a 42s run** — the real optimization target, not pip.
- **`push:` is still unfiltered**, so a PR'd branch fires two identical runs (once for `push`, once for
  `pull_request`). Now that the ruleset forces every change through a PR, `push: branches: [main]` is safe.
- **`strict_required_status_checks_policy: false`** — a PR's green tick can go stale if `main` moves after it
  ran. "Require branches to be up to date" closes that; costly on a team, free solo.
- **Single job, single Python version.** A `matrix:` would test 3.11/3.12/3.13 in parallel if I cared.
- **Nothing builds the Docker image in CI** — so `Dockerfile` breakage still wouldn't be caught. That comes
  with CD (Day 22).

---

## 🎤 Interview cheat-sheet

**"What does your CI do?"**
> Every push and PR spins up a clean Ubuntu runner, installs Python 3.12 and pinned deps, starts a Postgres 16
> service container with a `pg_isready` health check, and runs a 24-test pytest suite against a real database —
> not SQLite, because that diverges from prod. A repository ruleset makes that check required, so a red run
> can't be merged into `main`.

**"Why a real Postgres instead of mocking or SQLite?"**
> My tests use `TRUNCATE ... RESTART IDENTITY CASCADE`, `timestamptz`, and a `GROUP BY`/`HAVING` query with
> `COALESCE(max(...))`. SQLite would either reject or silently behave differently on all three. A container
> costs 20 seconds and removes the entire class of "passed in CI, broke in prod."

**"What's a flaky test and how do you avoid one?"**
> The classic in CI is racing a service container: Actions marks it started before Postgres accepts
> connections, so you get intermittent `Connection refused` that *passes on re-run*. The fix is a health check
> the runner blocks on. Generally: flakiness is an unstated dependency on timing, ordering, or shared state.

**"How do you keep secrets out of CI?"**
> Test credentials that name a throwaway container are plaintext in the workflow — they grant access to nothing
> that outlives the job. Anything that outlives the job goes in `${{ secrets.X }}`. The test is "does this grant
> access to something that still exists tomorrow," not "does it look like a password."

**"What does CI actually buy you?"**
> Regression catching is the obvious half. The underrated half is that it forces reproducibility — a runner has
> none of my undocumented laptop setup, so every hidden dependency has to become explicit or the build fails.
> It found a stray Day-2 script that was aborting collection, and it proved my deps install on 3.12, which is
> what my Dockerfile ships and my local venv (3.9) never tested.

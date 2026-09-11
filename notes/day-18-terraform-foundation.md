# 🏗️ Day 18 — Terraform fundamentals + first Azure resource

> **What I built:** `infrastructure/main.tf` — provider pinning, the azurerm provider, and one resource group,
> created for real in Azure with `terraform apply`. Plus the mental model for what Terraform actually *is*.
>
> **Status: DONE (2026-09-11).** Remote state now lives in an Azure blob with locking — see the bottom.

**Versions:** Terraform `v1.16.1` · azure-cli `2.90.0` · azurerm provider `4.81.0`
**Region:** `canadacentral` (Toronto — closest to Windsor, and the answer to "where does the data live?" for
Canadian enterprise, which is who I'm applying to)

---

## 🗣️ Plain-language version — start here when revising

### The problem: clicking

I could build all of this in the Azure portal. It works, and then:

- **Three weeks later, what's actually running?** Nobody knows. It's spread across twelve blades, mixed with
  things I made while debugging and forgot.
- **Rebuild it in another region?** Repeat every click from memory and hope I set the same firewall rule.
- **What changed Tuesday, and who did it?** Unanswerable.
- **Review a change before it goes live?** There's nothing to review — the change *is* the click.
- **Tear it down to stop the bill?** Delete things one at a time and pray. Orphaned disks and public IPs bill
  quietly for months.

All five are the same missing thing: **infrastructure that exists only as clicks has no source of truth.**

### The fix: a file that describes what should exist

```hcl
resource "azurerm_resource_group" "main" {
  name     = "rg-webhook-delivery-dev"
  location = "canadacentral"
}
```

Nothing in that says "create." It's a **claim about how the world should look**, and Terraform's job is to make
the world match it.

| Imperative (`az`, clicking) | Declarative (Terraform) |
|---|---|
| "create a resource group" | "a resource group should exist" |
| running it twice = error or duplicate | running it twice = **nothing happens** |
| I track what I've done | Terraform tracks it |
| to undo, remember and reverse every step | `terraform destroy` |

> **`terraform apply` twice in a row gives `No changes. Your infrastructure matches the configuration.`**
> That idempotence is what everything else is built on — it's why apply can run on every merge without fear,
> and why "destroy it Friday, rebuild it Monday" is routine instead of terrifying.

### Why it matters for *this* project

My cost strategy is **apply → verify → record the demo → `terraform destroy`**, so I don't pay for idle
infrastructure. That's only possible because the infrastructure is code. **The portfolio artifact is the
`infrastructure/` directory, not a running server** — a reviewer can read exactly what I built, and I can bring
it back in ten minutes for an interview.

---

## 🔑 The mental model — Terraform compares three things

| | What it is | Who writes it |
|---|---|---|
| **Config** (`.tf`) | what *should* exist | me |
| **State** (`terraform.tfstate`) | what Terraform *believes it created* | Terraform |
| **Reality** (Azure) | what actually exists | the world |

```
      ┌── config ──┐
      │            │
   plan diffs    apply makes reality match config,
   all three     then updates state to match
      │            │
   state ──── reality
```

The commands are just operations on that triangle:

| Command | What it does |
|---|---|
| `init` | download providers, set up the backend. Once per config, and again when providers/backend change |
| `plan` | refresh state from reality, diff against config, print what it *would* do. **Changes nothing** |
| `apply` | do it, then record the result in state |
| `destroy` | treat the config as empty — remove everything state says it owns |

Everything confusing about Terraform is a mismatch in that triangle:

- Someone edits in the portal → **reality ≠ state** → next plan proposes to undo it. That's **drift**.
- Delete state → Terraform forgets it owns anything → tries to create duplicates → Azure rejects them.
- A resource exists but state doesn't know → `terraform import` teaches state about it.

> **Terraform doesn't manage Azure. It manages the gap between my file and Azure.**

---

## How to write blocks

### The grammar — this is the entire syntax

```
BLOCK_TYPE  "label"  "label"  {
  argument = value
  nested_block { ... }
}
```

Only the number of labels changes.

| Block | Labels | Means |
|---|---|---|
| `terraform` | 0 | settings for Terraform itself — versions, backend |
| `provider "azurerm"` | 1 | configure a plugin |
| `resource "azurerm_resource_group" "main"` | 2 | **something Terraform creates and owns** |
| `data "azurerm_client_config" "current"` | 2 | something that already exists — **read-only lookup** |
| `variable "location"` | 1 | an input |
| `output "rg_name"` | 1 | a value to surface after apply |
| `locals` | 0 | named intermediate values |

`resource` and `data` take two labels for the same reason: **type**, then **my name for it**. `resource` says
"make this exist"; `data` says "find this, I need to read from it."

Rules: `name = value`, **no commas**, double-quoted strings, `#` comments. `terraform fmt` settles every
whitespace argument permanently — 2-space indent, `=` aligned *per block* (so a long name in one block never
reflows another; stable diffs).

### 🔑 The idea that makes it click: labels are addresses

```hcl
resource "azurerm_storage_account" "files" {
  name                = "stnotesfiles4821"
  resource_group_name = azurerm_resource_group.main.name       # ← reference
  location            = azurerm_resource_group.main.location   # ← reference
}
```

`azurerm_resource_group.main.name` is an **address**: type · my label · attribute. And a reference does more
than substitute a string:

> **Every reference is an edge in a dependency graph.**

I never tell Terraform "make the resource group first." It reads that the storage account *depends on* the
resource group's attributes, builds a DAG, orders the work itself, creates unrelated things in parallel, and
reverses the order on destroy. **Wiring resources by reference instead of copying literals is the difference
between a config that works and one that races.**

(`depends_on` exists for dependencies not visible in the arguments. Needing it usually means I copied a value I
should have referenced.)

### Variables and outputs

`var.location` reads an input; `output` surfaces a value after apply and is how one config hands values to
another. **Don't add variables preemptively** — a variable with one possible value is indirection with no
payoff. Add one when a value is genuinely environment-specific (`dev` vs `prod`) or is a secret from outside.

---

## 📖 Reading a plan — the symbol that matters

```
Plan: 1 to add, 0 to change, 0 to destroy.
```

| Symbol | Meaning | Danger |
|---|---|---|
| `+` | create | safe |
| `~` | update **in place** | usually safe |
| `-` | destroy | obvious |
| **`-/+`** | **destroy and recreate** | 🚨 **this is the one** |

`-/+` appears when I change an attribute that can't be altered on a live resource — a Postgres server's name, a
subnet's address range. Terraform's answer is delete-then-rebuild, **and the data goes with it.** In a wall of
output it looks almost identical to `~`.

> **Reading the plan IS the safety mechanism** — not a formality before the interesting command. On Day 19
> there's a database in here, and `-/+ azurerm_postgresql_flexible_server` buried in 200 lines is exactly how
> people lose one.

**`(known after apply)`** = a value Azure assigns, unknowable until the resource exists. Those are how one
resource's output feeds another's input.

**The `-out` note is a real race, not boilerplate.** Between `plan` and `apply` the world can change.
`plan -out=tfplan` freezes the decision; `apply tfplan` executes exactly that with no re-planning. Locally it
matters little (bare `apply` re-plans and prompts). **In CI it's mandatory:** the pipeline plans, a human
reviews *that plan*, and apply runs the reviewed artifact — not whatever the world looks like ten minutes later.
(Day 22.)

---

## How Terraform authenticates to Azure

**Nothing in `main.tf` says who I am or where to deploy.** That's deliberate: config describes *what* (in git),
the environment supplies *where and as whom* (never in git) — the same separation as the `env:` block in my CI
workflow.

| Need | Where it comes from |
|---|---|
| **Who I am** | the `az login` token cache in `~/.azure/` |
| **Which subscription** | the `ARM_SUBSCRIPTION_ID` env var |

```bash
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
```

The whole `ARM_*` family works this way — `ARM_TENANT_ID`, `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`, `ARM_USE_OIDC`.
Anything that *could* go in the `provider` block can come from an env var instead, which is how CI will supply
it without editing the file.

**Why the subscription is mandatory in azurerm 4.x:** v3 quietly inherited whatever subscription the CLI had
selected. Convenient — and an excellent way to create resources in the wrong subscription because I'd run
`az account set` two days earlier and forgotten. v4 makes me state it. **It's per-terminal**; a new tab means
re-exporting, and "Terraform can't find a subscription" is always this.

Auth modes, for later:

| Mode | Used for |
|---|---|
| **Azure CLI** | local dev — what I'm on now |
| Service principal + client secret | CI, the old way — a password in a secret store |
| Managed identity | code running *inside* Azure |
| **OIDC / workload identity federation** | CI, the modern way — **Day 22** |

OIDC is why Actions → Azure is nicer than Actions → AWS: GitHub proves the workflow's identity with a
short-lived token, so **there's no long-lived secret stored in GitHub at all.** Nothing to leak or rotate.

**Control plane vs data plane** — Azure separates managing a resource from reading/writing its *contents*.
Being subscription **Owner** grants the first but **not automatically** the second, so
`az storage container create --auth-mode login` can fail with `AuthorizationPermissionMismatch` on an account I
own. `--auth-mode key` has the CLI fetch the account key, which Owner *is* allowed to do. This split comes back
on Day 20 with Key Vault.

---

## 📦 What state actually is

After the first apply, `terraform.tfstate` — 1,194 bytes of readable JSON:

```
version        : 4                    ← state file format
terraform_ver  : 1.16.1
serial         : 1                    ← bumped on every write
lineage        : 1f5f2633...          ← UUID identifying THIS state's family
resources      : 1
  managed  azurerm_resource_group.main
      id       = "/subscriptions/…/resourceGroups/rg-webhook-delivery-dev"
      location = "canadacentral"
      name     = "rg-webhook-delivery-dev"
```

Two fields do quiet safety work: **`serial`** increments on every write so a backend can detect "someone wrote
since you read," and **`lineage`** identifies the state's identity so Terraform refuses a state file from a
different family instead of assuming I meant it.

> **State is just a map from my config's names to real Azure ids.** `id` is how Terraform re-finds the resource;
> everything else is what it believed at last apply.

### What goes in git

| File | Git? | Why |
|---|---|---|
| `.terraform/` | **ignore** | downloaded provider binaries — hundreds of MB, rebuilt by `init` |
| `.terraform.lock.hcl` | ✅ **commit** | exact provider versions + checksums |
| `terraform.tfstate` | 🚫 **never** | plaintext secrets, and it's a shared mutable database |
| `*.tfvars` | ignore | environment-specific values, often secrets |

```gitignore
# Terraform
**/.terraform/*
*.tfstate
*.tfstate.*
*.tfvars
crash.log
```

`**/.terraform/*` rather than `.terraform/` so it still matches once there's an `infrastructure/modules/`.

**The lock file is the one people get wrong** — it looks like generated junk, gets ignored, and then CI resolves
`~> 4.0` to a newer patch than I'm on and something behaves differently for reasons nobody can reproduce. It's
`requirements.txt`'s pins, or `package-lock.json`. Terraform's own `init` output says to commit it.

**And `terraform.tfstate` is the dangerous one**, for two independent reasons:

1. **Every attribute is stored in plaintext** — including the Postgres admin password on Day 19. Committing
   state is one of the most common ways real credentials reach a public repo.
2. **It's a database, not a document.** Two applies at once = conflicting writes with no locking, and corrupted
   state means Terraform has forgotten what it owns.

Reason 2 is exactly what remote state fixes.

---

## 🧭 Decision: the state backend lives OUTSIDE Terraform

State can't live on my laptop: lose it and Terraform forgets it owns anything; CI can't read it on Day 22; and
two applies have no locking. The fix is a **backend** — state in an Azure blob, with blob leases giving locking
for free.

**Bootstrap problem:** Terraform can't store state in a storage account that doesn't exist yet. Two ways out:

- **(a) Create it with Terraform, then migrate state into it** — everything-in-code, philosophically tidy.
- **(b) Create it once with `az`, outside Terraform** ← **chose this.**

Normally (a) is tempting, but my workflow decides it: the plan is **apply → verify → record → `terraform
destroy`**. Under (a), `destroy` tries to delete the storage account holding its own state — sawing off the
branch I'm sitting on.

> **The state backend is the one thing that must outlive everything Terraform manages, so it shouldn't be
> managed by it.**

Its own resource group too (`rg-terraform-state`), so a stray `az group delete` on the app's group can't take it.

---

## 💳 Azure account facts (free trial)

```
quotaId       : FreeTrial_2014-09-01
spendingLimit : On
state         : Enabled
credit        : C$277.21, expires October 9, 2026
```

- **The $200 vs $277 confusion was currency.** $200 is Microsoft's USD marketing figure; the account bills in
  **CAD**, so the grant is a localized CAD amount (277 ÷ 200 ≈ 1.385 — a normal USD→CAD rate). Not a top-up.
- **`spendingLimit: On` — keep it.** When credit runs out Azure **disables resources instead of charging me.**
  No surprise bill. Upgrading to pay-as-you-go removes it, which is the point of upgrading.
- **The real constraint is the 30-day clock, not the dollars.** Days 19–23 will use a fraction of C$277
  (Container Apps free grant, Postgres B1ms 12-month free offer, state storage is pennies). What can run out is
  *time* — the credit dies **Oct 9** regardless of balance, and it's one free trial per person.

---

## 📌 Plan change from the original Phase 9 schedule

Original Day 18 was *"Terraform fundamentals + remote state + VNet."* **Dropped the VNet.**

A VNet with nothing in it teaches nothing — networking decisions are made *by* what lives in them (does Postgres
get a private endpoint? does Container Apps need a delegated subnet?), and I'd have been guessing before
creating either. Building it on Days 19–21 alongside its occupants means every subnet exists for a reason I can
articulate.

---

## ✅ Remote state — DONE (2026-09-11)

Bootstrapped the backend out-of-band, exactly as the decision above requires:

```bash
az group create --name rg-terraform-state --location canadacentral
az storage account create --name stwhdeliverytf4821 --resource-group rg-terraform-state \
  --location canadacentral --sku Standard_LRS --min-tls-version TLS1_2 \
  --allow-blob-public-access false
az storage container create --name tfstate --account-name stwhdeliverytf4821 --auth-mode key
```

Then a `backend "azurerm"` block inside the `terraform {}` block — **a sibling of `required_providers`, because
it is a setting about Terraform itself, not about Azure** — and:

```bash
terraform init -migrate-state     # answered "yes" to copying local state into the blob
```

### The migration, proved by the numbers

```
terraform.tfstate            0 bytes   <- local file is now an empty stub
terraform.tfstate.backup  1194 bytes   <- pre-migration copy
webhook-delivery.tfstate  1194 bytes   <- the blob in Azure
```

**1194 → 1194.** The blob is byte-for-byte the state that was on the laptop. The local file being empty is
correct, not a failure: the blob is authoritative now.

### What changed in the output, and why it matters

```
$ terraform state list
azurerm_resource_group.main          <- read out of the BLOB, not the local file

$ terraform plan
No changes. Your infrastructure matches the configuration.
Releasing state lock. This may take a few moments...
```

That last line **did not exist before the migration**. It is the **blob lease** being taken and released around
the operation — locking, for free, as a property of where the state lives. That is the thing that makes it safe
for a CI pipeline to run `apply` on Day 22, and it is the whole reason remote state came before the cloud
resources rather than after.

> Also confirmed: `--allow-blob-public-access false` and `TLS1_2` took on the storage account. Not cosmetic —
> that blob holds the Postgres admin password from Day 19 onward.

## ⏭️ Next

**Day 18 is complete.** `infrastructure/main.tf` holds the `terraform` / `provider` / `resource` blocks plus the
`azurerm` backend; `rg-webhook-delivery-dev` is live in `canadacentral`; state is in the blob with locking.

**Day 19:** Azure Container Registry, build and push the image, and a Postgres Flexible Server. That is the day
the state file starts holding a real secret — which is why `--allow-blob-public-access false` went on the
storage account before any of it.

**Still to commit from Day 18:** `infrastructure/` (main.tf + `.terraform.lock.hcl` — the lock file IS committed,
the `.terraform/` directory and every `*.tfstate*` are not), the `.gitignore` Terraform block, and this note.
Branch `feat/terraform-foundation`, then a PR — the ruleset from Day 17 blocks pushing straight to `main`.

**Azure credit clock:** trial credit expires **9 Oct 2026**. Days 19-23 need to land inside that window.

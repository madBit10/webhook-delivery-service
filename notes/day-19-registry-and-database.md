# 🗄️ Day 19 — Container registry, the image, and a cloud database

> **What I built:** an Azure Container Registry, the first push of my app image into it, and a PostgreSQL
> Flexible Server with my five Alembic migrations applied — all of it declared in Terraform except the image
> itself.
>
> **The day's real lesson:** what happens to a secret once Terraform touches it.

**Region:** `canadacentral` · **Registry:** `acrwhdelivery4821` · **Server:** `psql-whdelivery-4821` (PG 16, B1ms)

---

## 🗣️ Plain-language version

Three things exist now that didn't before:

1. **A registry** — a private Docker Hub that only I can push to and only my Azure resources can pull from.
2. **An image in it** — `webhook-api:v1`, the same Dockerfile I wrote on Day 1, finally used for something.
3. **A database in the cloud** — with my real schema in it, migrated from empty by the same Alembic chain
   that runs locally.

The app isn't running yet. Day 21 does that. Today was building the things it will need.

---

## Splitting the config

Terraform **loads every `.tf` file in the directory and concatenates them.** There is no import, no ordering,
no manifest. Splitting is purely for humans:

```
infrastructure/
├── main.tf        # the resources
├── variables.tf   # inputs
└── outputs.tf     # values to surface after apply
```

I could have kept it in one file and nothing would change. I split it because `main.tf` was about to triple in
size, and because inputs and outputs are what someone reads first when they meet a config they didn't write.

---

## 🔑 THE DAY'S LESSON: `sensitive` is about printing, not storing

The password had to get from my head to Azure. Options:

| Where | Why not |
|---|---|
| hardcoded in `main.tf` | it's in git forever, in every clone, including ones I can't delete |
| a `terraform.tfvars` file | gitignored — but one `git add -A` from committed, and plaintext on disk |
| **`TF_VAR_` environment variable** | ← what I used |
| Azure Key Vault | Day 20 |

Terraform reads any env var named **`TF_VAR_<variable name>`** into the matching variable. The value never
touches a file, and CI can supply the same way from a GitHub secret (Day 22).

```hcl
variable "db_admin_password" {
  type      = string
  sensitive = true     # redacts it from plan/apply OUTPUT
  # no default — so Terraform STOPS and asks rather than using something wrong
}
```

**A variable with no `default` is a deliberate choice.** It makes the missing value an error instead of a
silent fallback.

### Then I looked in the state file

```
administrator_login    = "whadmin"
administrator_password = "0*****************************k"   <-- 32 chars, PLAINTEXT
fqdn                   = "psql-whdelivery-4821.postgres.database.azure.com"
```

> **`sensitive = true` controls what Terraform PRINTS. It does nothing to what Terraform STORES.**

That single fact retroactively justifies two Day-18 decisions I made before I had anything to protect:

- `--allow-blob-public-access false` on the state storage account
- `*.tfstate` in `.gitignore`

If state were in git, my database password would now be in every clone of the repo. Permanently.

**The convenient flip side:** I can recover a lost password with
`terraform state show azurerm_postgresql_flexible_server.main | grep administrator_password`.
Useful — and exactly why anyone with read access to state effectively owns the database.

### `administrator_password_wo` — the modern answer

The plan showed an attribute I never wrote: `administrator_password_wo = (write-only attribute)`.
Terraform 1.11 added **write-only arguments** — values sent to the API and then *deliberately never written to
state*. That is the real fix for this problem.

I used the classic `sensitive` variable anyway, for two reasons: it's still what most real codebases look like,
and the pain it leaves behind is what motivates Key Vault tomorrow.

---

## ⚠️ The arm64 trap (checked BEFORE it could bite)

```
uname -m     → arm64      (Apple Silicon)
Azure runs   → amd64
```

A plain `docker build` on this machine produces an **arm64 image**. Push it, deploy it, and the container fails
to start with `exec format error` — a message that says nothing about architecture. People lose hours here,
and the failure is silent until deploy time (Day 21), far from the cause (Day 19).

```bash
docker build --platform linux/amd64 -t acrwhdelivery4821.azurecr.io/webhook-api:v1 .
```

Verified rather than assumed:

```bash
docker image inspect ...:v1 --format '{{.Os}}/{{.Architecture}}'   # linux/amd64 ✓
```

### Tagging

`acrwhdelivery4821.azurecr.io/webhook-api:v1` is three parts — **registry host / image name : tag**. Docker
uses the hostname prefix to decide where to push; without it, it goes to Docker Hub.

**`:v1`, not `:latest`.** `latest` is an ordinary tag that merely *looks* like a pointer to the newest build.
Deploy it and you cannot tell which build is running, and a rollback has nothing to roll back *to*. Immutable
tags are what make Day 22's CD honest.

### `admin_enabled = false`

ACR can have a shared admin username and password, and most tutorials turn it on. I didn't need it:
`az acr login` authenticates as *me* through Azure AD, and Day 21 will let Container Apps pull with a managed
identity. Shared static credentials are the thing you'd have to rotate and could leak — not creating them is
simpler than protecting them.

---

## 🐛 The escaping bug — two layers disagreeing about `%`

Azure Postgres requires TLS, and my `openssl rand -base64` password contained `/`. So I percent-encoded it into
the connection URL. Then:

```
ValueError: invalid interpolation syntax in 'postgresql://whadmin:...%2F%2Fk@...' at position 50
```

The chain:

1. `/` in a URL is a path separator → must be encoded as `%2F`
2. `alembic/env.py:16` passes the URL to **configparser**
3. configparser has its own syntax where `%` begins an interpolation (`%(name)s`)
4. It sees `%2F`, tries to interpret it, dies

**Each layer is individually correct and they disagree about what `%` means.** That is the shape of every
escaping bug. I could have escaped again (`%%2F`) and it would have worked — but stacking a third layer of
escaping to survive the second is how configs rot.

### The fix removes the class, not the instance

Generate a password containing nothing that is special to a URL, a shell, or configparser:

```bash
LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32
```

Uppercase + lowercase + digits = **three character classes**, which satisfies Azure's complexity requirement.
(`openssl rand -hex` would NOT — hex is lowercase and digits only, two classes, and Azure rejects it.)

Then no encoding step exists at all, so no layer can disagree with another.

**Also: the traceback printed the password to my terminal**, so I rotated it. Changing it was
`~ administrator_password` — an **update in place**, `0 to add, 1 to change, 0 to destroy`. Worth checking that
symbol carefully: `-/+` on a database resource means destroy and rebuild, and the data goes with it.

---

## The resources

```hcl
resource "azurerm_container_registry" "main" {
  name                = "acrwhdelivery4821"   # NO HYPHENS — ACR is alphanumeric-only
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
}
```

**ACR forbids hyphens**, unlike almost every other Azure resource. 5–50 chars, globally unique.

```hcl
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
```

**That is not a range — it's a sentinel.** Azure special-cases `0.0.0.0`–`0.0.0.0` to mean "allow connections
originating inside Azure." It looks like "allow the entire internet" and means the opposite. Genuinely bad API
design; worth recognising in someone else's config.

The other rule allows my own IP, which is what lets me run Alembic from my laptop.

---

## ✅ How I verified it

Every claim checked from outside Terraform:

```
az postgres flexible-server list   → psql-whdelivery-4821  Ready  PG 16  Standard_B1ms
az postgres flexible-server db list → webhooks ✓ (+ azure_maintenance, postgres, azure_sys)
az acr repository show-tags         → v1
docker image inspect                → linux/amd64
state blob                          → 1,194 → 10,477 bytes
```

Then the migrations, pointed at the cloud by exporting `DATABASE_URL` — **pydantic-settings ranks environment
variables above the `.env` file**, so one export redirects Alembic without editing anything.

> **Migrating a cloud database from empty is the first honest test of the migration chain.** Locally I have
> been mutating one database for three months; it works because of the order things happened in, not
> necessarily because the chain is correct. From empty, in order, on a fresh server, is the real proof.

---

## ⚠️ Known limits / next

- **Public network access is on.** The database is reachable from the internet, gated only by firewall rules.
  Real deployments use a private endpoint and a VNet. Acceptable for a demo; not for production.
- **The password is in state.** → Day 20, Key Vault.
- **Nothing is running yet.** The image sits in a registry; the database is empty of traffic. → Day 21.
- **The image was built and pushed by hand.** → Day 22 makes that a pipeline.
- **Azure credit expires 9 Oct 2026.** Days 20-23 must land inside that window.

---

## 🎤 Interview cheat-sheet

**"How do you handle secrets in Terraform?"**
> Short answer: carefully, because state holds them in plaintext. `sensitive = true` only redacts output — it
> doesn't encrypt storage. So the state backend has to be treated as a secret store in its own right: private
> blob, no public access, never in git, access-controlled. Then you move the actual secret out to a vault and
> have Terraform reference it rather than carry it. Terraform 1.11's write-only arguments are the newer answer,
> where the value never lands in state at all.

**"Why does your Dockerfile build specify a platform?"**
> I develop on Apple Silicon and deploy to amd64 Linux. Without `--platform linux/amd64` the image builds for
> arm64 and fails at deploy with `exec format error`, which doesn't mention architecture anywhere. It's a
> silent failure that surfaces two days after the mistake, so I verify the built architecture rather than
> assume it.

**"Why not `:latest`?"**
> Because it isn't a pointer to the newest build, it's a tag that looks like one. If production runs `latest`
> you can't identify which build is live and a rollback has no target. Immutable tags make deploys and
> rollbacks both addressable.

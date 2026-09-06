# Lioxi Dashboard

Self-hosted portal for Azure OpenAI / Azure AI Foundry fleets. It monitors token usage and cost, onboards member subscriptions through a Join wizard, deploys Kimi K3, and talks to Telegram for spend alerts and ops.

The production endpoints your models serve are never proxied. Azure identities used for monitoring stay read-only. Deploy and Join create their own service principals when you ask them to.

## What you can do

| Page | Purpose |
| --- | --- |
| **Overview** | Usage and cost charts, estimated vs Azure billed spend, USD/INR toggle |
| **Accounts** | Add Azure tenants, discover Cognitive Services / Foundry resources, group accounts, sync now |
| **Deploy K3** | Upload Azure credentials, deploy Kimi K3, attach NewAPI channels, scale TPM tiers, sync a Google Sheet inventory |
| **Pending** | Approve or reject Join submissions (creates the monitoring SP and optional autodeploy) |
| **Ban** | Enrollee allow-list, ban names, toggle Join auto-approve |
| **Models** | Register deployments and per-million-token pricing |
| **Alerts** | NewAPI spend headroom, payable export, Telegram test/group messages |
| **Join** (`/join`) | Password-gated public wizard: Azure device-code login, pick a subscription, pick a name |

The sidebar also shows live host CPU / RAM / disk so you can see when Az CLI or deploy jobs are saturating the box.

## How monitoring works

Each Azure account is watched through a **read-only service principal** (tenant ID, client ID, client secret, subscription ID) with `Reader`, `Monitoring Reader`, `Cost Management Reader`, and related read roles.

The backend polls:

- **Azure Monitor** for per-deployment token counts (prompt / cached / completion / total, request count)
- **Azure Cost Management** for actual billed cost per account per day
- **NewAPI O1 / O2** (optional) for gateway spend and channel status

Cost on the dashboard has two sources:

- **Estimated** — token counts × the prices you enter per model
- **Actual billed** — Azure Cost Management at account scope (Azure does not split billing by deployment)

Schedulers (defaults, overridable on Alerts):

- NewAPI sync every **5 minutes**
- Azure token/cost sync every **30 minutes**
- Manual full sync from Accounts

Usage is account- and deployment-level. Azure does not expose per-human-user attribution on these APIs.

## Architecture

```
backend/app/
  core/            Fernet crypto, password hashing, JWT
  models/          SQLAlchemy (accounts, models, snapshots, Join, SPs)
  providers/       CloudMetricsProvider + Azure ARM / metrics / cost
  repositories/    data access
  routers/         FastAPI: auth, accounts, deploy, join/pending/ban, alerts, telegram
  services/        sync, Join, Deploy K3, NewAPI, Telegram, Sheets, system stats
  main.py          app wiring + schedulers + Telegram webhook

frontend/src/
  pages/           Overview, Accounts, Deploy, Pending, Ban, Models, Alerts, Join, Login
  hooks/           React Query per resource
  components/      layout, charts, accounts, deploy, join
```

Stack: FastAPI + Postgres + Redis, React/Vite UI behind nginx. Docker Compose is sized for a **4 vCPU / 8 GiB** host.

Adding another cloud means implementing `CloudMetricsProvider` and registering it in `providers/registry.py`.

## Prerequisites

- Docker and Docker Compose
- Azure service principals (read-only for monitoring; Join/Deploy create extras when used)
- Optional: NewAPI gateways, Telegram bot, Google service-account for the inventory sheet

## Setup

1. Copy env and generate secrets:

   ```bash
   cp .env.example .env
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY
   openssl rand -hex 32                                                                          # JWT_SECRET
   ```

   Set `ADMIN_USERNAME` / `ADMIN_PASSWORD` (seeded on first boot). Set `JOIN_PASSWORD` if you want `/join` open to members.

   Optional: `NEW_API_*` / `NEW_API2_*`, `TELEGRAM_*`, `GOOGLE_SHEETS_*`. Leave blank to skip those integrations.

   Name tags can be typed when you add an account. To auto-tag from a local Name,Endpoint sheet, copy `backend/app/data/imp_data.csv.example` to `backend/app/data/imp_data.csv` on this machine only.

2. Start:

   ```bash
   docker compose up -d --build
   ```

   - UI: http://localhost:3000
   - API: http://localhost:8000 (`/docs`)
   - Join: http://localhost:3000/join
   - Postgres and Redis stay on the compose network

3. Log in with the admin credentials from `.env`.

### Add an Azure account to monitor

```bash
az ad sp create-for-rbac --name "usage-monitor" --skip-assignment --years 1

APP_ID="<appId from above>"
SUB="/subscriptions/<subscription-id>"
for role in "Monitoring Reader" "Cost Management Reader" "Reader" "Cognitive Services Usages Reader" "Billing Reader"; do
  az role assignment create --assignee "$APP_ID" --role "$role" --scope "$SUB"
done
```

Then **Accounts → Add account**: tenant ID, client ID, client secret, subscription ID → **Discover resources** → pick a Cognitive Services / Foundry resource.

**Models → Add model**: pick the account, pick a live deployment, enter pricing per million tokens. Sync on the next scheduler run or **Sync now**.

### Join and Pending

1. Member opens `/join`, unlocks with `JOIN_PASSWORD`, signs in with Azure device code, picks a subscription and an enrollee name.
2. The portal creates a service principal and waits for approval (or auto-approves if enabled on **Ban**).
3. Admins review **Pending**. Approve deploys Kimi when autodeploy is on; reject drops the request.
4. **Ban** holds the enrollee list and a global auto-approve toggle.

### Deploy K3

On **Deploy K3**, paste or drop Azure credential JSON (never commit those dumps). The job can create Foundry resources, attach the Lioxi content filter, register NewAPI channels, scale quota tiers, and push inventory to a Google Sheet when `GOOGLE_SHEETS_*` is set.

Mount the service-account JSON at `secrets/google-sheets.json` (already gitignored). Share the sheet with that service account as Editor.

### Telegram

If `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, the backend registers a webhook at `/api/telegram/webhook`. Put the TLS cert/key in `secrets/telegram-webhook.crt` and `secrets/telegram-webhook.key`. Admin IDs can run bot commands in the group and in DMs; owner IDs default to the same list.

## Secrets — keep these off git

Tracked files are templates and code only. This repo must never contain live credentials.

| Local only | Why |
| --- | --- |
| `.env` | Portal, NewAPI, Telegram, Sheets, SSH sync passwords |
| `secrets/` | Google service-account JSON, Telegram TLS cert/key |
| `google-sheets.json` | Same key if dropped at repo root |
| `*.csv` (except `*.csv.example`) | Member/endpoint mapping |
| `/*.json` at repo root | SP / secret dumps (`new_final.json`, deploy leftovers) |
| `*.pem` `*.key` `*.p12` `*.pfx` | Private keys |
| `delete_after_git_setup_done.txt` | One-off PAT drop files |

`.env.example` and `imp_data.csv.example` are safe placeholders.

Azure client secrets, Foundry keys, and NewAPI tokens are encrypted in Postgres with `ENCRYPTION_KEY`. They are not written back to the git tree.

`scripts/sync_prod_db.sh` reads `PROD_SSH_*` from `.env`. It has no baked-in host, user, or password.

If a secret ever lands in a commit, rotate it and remove it from history. Do not rely on a later delete.

## Notes

- Tables are created with `Base.metadata.create_all` on startup. There is no Alembic yet; add it before changing schema on a database you care about.
- Cost Management is best-effort. Some subscriptions rate-limit or block it. Estimated cost still works; billed cost may show 0.
- Az CLI is the memory hog. The compose file caps the backend at 4 GiB and limits concurrent deploy / Az CLI work for a 4 vCPU / 8 GiB host.
- License: MIT.

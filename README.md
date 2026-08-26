# LLM Usage Monitoring Portal

A self-hosted dashboard for monitoring token usage and cost across multiple Azure
OpenAI / Azure AI Foundry accounts, with per-model pricing you control.

## How it works

Each Azure account is monitored through a **read-only service principal** (tenant
ID + client ID + client secret + subscription ID) granted only `Reader`,
`Monitoring Reader`, and `Cost Management Reader` roles. The backend polls:

- **Azure Monitor** (`Microsoft.Insights/metrics`) for per-deployment token counts
  (prompt/cached/completion/total tokens, request count).
- **Azure Cost Management** for actual billed cost per account per day.

This identity can never call, modify, or redeploy any model — it only reads
platform telemetry. **Your production endpoints, API keys, and base URLs are
never touched.** There is no proxy in front of your models.

Because Azure doesn't expose per-end-user attribution on these APIs, usage is
tracked at **account** and **model/deployment** granularity, not per human user.
Cost shown on the dashboard has two sources:

- **Estimated cost** - computed from the token counts above using the pricing
  you enter for each model.
- **Actual billed cost** - the real number from Azure Cost Management, at the
  account level (Azure doesn't split billing by deployment).

A background scheduler re-syncs NewAPI spend and channel status on an
interval (default 5 minutes, configurable on the Alerts page). Azure
token/cost sync runs separately on a slower schedule. You can also trigger
a full sync on demand from the Accounts page.

## Architecture

```
backend/app/
  core/            crypto (Fernet), password hashing + JWT, exceptions
  models/          SQLAlchemy ORM (accounts, models, usage/cost snapshots, admin)
  providers/       CloudMetricsProvider interface + Azure implementation
                    (token acquisition, ARM client, discovery, metrics, cost)
  repositories/    data access layer (one per aggregate)
  services/        business logic (accounts, models, pricing, sync, dashboard, auth)
  routers/         FastAPI HTTP layer
  main.py          app wiring + scheduler

frontend/src/
  components/ui/   design-system primitives (Button, Card, Modal, ...)
  components/...   feature components (accounts, models, charts)
  pages/           route-level pages
  hooks/           React Query hooks per resource
  context/         auth context
```

The provider layer is built around a `CloudMetricsProvider` abstract interface
(`backend/app/providers/base.py`). Adding a new cloud provider (e.g. a direct
OpenAI usage API) means implementing that interface and registering it in
`providers/registry.py` - no other code needs to change.

## Prerequisites

- Docker and Docker Compose
- One or more Azure service principals with read-only roles on the
  subscriptions you want to monitor (see below)

## Setup

1. Copy the example env file and fill in secrets:

   ```bash
   cp .env.example .env
   ```

   Generate the two required secrets:

   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY
   openssl rand -hex 32                                                                          # JWT_SECRET
   ```

   Set `ADMIN_USERNAME` / `ADMIN_PASSWORD` for your own login - this admin
   account is seeded automatically on first startup.

   Optional: fill `NEW_API_*` / `NEW_API2_*` for gateway spend sync, and
   `TELEGRAM_*` for group alerts. Leave them blank to run the portal without
   those integrations.

   Name tags can be typed when you add an account. To auto-tag from a local
   Name,Endpoint sheet, copy `backend/app/data/imp_data.csv.example` to
   `backend/app/data/imp_data.csv` and fill it on this machine only. That file
   is gitignored — do not commit it.

2. Start everything:

   ```bash
   docker compose up -d --build
   ```

   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000 (docs at `/docs`)
   - Postgres: localhost:5432 (credentials data only, encrypted)

3. Log in with the admin credentials from `.env`.

## Adding an Azure account to monitor

For each Azure subscription you want to monitor, create a read-only service
principal once:

```bash
az ad sp create-for-rbac --name "usage-monitor" --skip-assignment --years 1

APP_ID="<appId from above>"
SUB="/subscriptions/<subscription-id>"
for role in "Monitoring Reader" "Cost Management Reader" "Reader" "Cognitive Services Usages Reader" "Billing Reader"; do
  az role assignment create --assignee "$APP_ID" --role "$role" --scope "$SUB"
done
```

Then in the portal:

1. **Accounts -> Add account** - enter the tenant ID, client ID, client
   secret, and subscription ID, then click **Discover resources**. The portal
   lists every Cognitive Services / AI Foundry resource that identity can see;
   pick one and save.
2. **Models -> Add model** - pick the account, pick a live deployment from the
   dropdown (fetched directly from Azure), and enter your pricing per million
   tokens (input, cached input, output). The model starts being synced on the
   next scheduler run, or immediately via **Sync now** on the Accounts page.

## Notes / trade-offs

- No Alembic migrations yet - tables are created automatically on startup via
  `Base.metadata.create_all`. For schema changes after you have real data,
  add Alembic.
- Cost Management is best-effort: some subscription types restrict or rate
  limit that API. If it fails, estimated cost (from your pricing) still works;
  only the "actual billed cost" figure will read as 0 for that account.
- Monitoring is per account/model, not per individual human user, since Azure
  does not expose that attribution outside of a request-level proxy - which
  was explicitly out of scope here since production base URLs must not change.

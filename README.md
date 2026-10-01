# Hoard Counter

A private household ledger for budgeting, spending, subscriptions, investment planning, and financial reports. Next.js/React/TypeScript frontend, Flask backend, and SQLite storage. Designed for a Linux Docker host reached over Tailscale.

## What is included

- Individual household logins with administrator/member permissions.
- Exact-decimal, double-entry transactions, editable history, refunds, native currencies, dated USD conversion rates, and asset/debt balances.
- Receipts and item images, categories/tags, recurring subscriptions, transaction rules, and monthly budgets.
- History-based expense forecasts, aggregate investment valuations, cash-flow-adjusted performance, and saved projection scenarios.
- Filtered CSV/PDF reports, editable CSV templates, previewed UUID/timestamp imports, and complete financial archives with attachments.
- Persistent Docker storage, Alembic migrations, verified weekly backups with calendar retention, and an upgrade/restore command.

## Run on Linux

See [the operations guide](docs/operations.md) for first installation, private HTTPS, scheduled backups, releases, and recovery. Docker images never contain the household database or uploads.

```sh
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
# Set SECRET_KEY in .env to the generated value.
sudo python3 scripts/manage.py deploy --version 1.0.0
sudo tailscale serve --bg http://127.0.0.1:8080
```

Open the private HTTPS address and create the first administrator. Start by adding financial accounts and opening balances. Built-in Income and Expenses counterparties are provided; add named payees and income sources as needed. For non-USD accounts, enter exchange rates before transactions or nonzero opening balances.

## Local development

Python 3.11+ and Node.js 22+ are required. Use disposable development data and a different secret from production.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
export DATA_DIR="$PWD/data"
export UPLOAD_DIR="$PWD/uploads"
export BACKUP_DIR="$PWD/backups"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export COOKIE_SECURE=false
cd backend
alembic upgrade head
flask --app whc:create_app run --host 127.0.0.1 --port 5000
```

In another terminal:

```sh
cd frontend
corepack enable
corepack prepare pnpm@11.23.0 --activate
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://127.0.0.1:3000`. The development frontend forwards API requests to Flask. Financial state stays in the directories above across frontend/backend restarts. `COOKIE_SECURE=false` is only for local HTTP development; production uses HTTPS and secure cookies.

## Validation and API

```sh
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python backend/tools/generate_contract.py
cd frontend
pnpm test
pnpm typecheck
pnpm build
```

The committed [OpenAPI contract](openapi.json) is served at `/api/v1/openapi.json` after login. Its TypeScript interfaces are generated into `frontend/lib/generated.ts`. Money travels as decimal strings; mutations require `X-CSRF-Token`, and edits/voids require the revision the caller last read.

Read [accounting and exchange behavior](docs/accounting.md) for currencies, returns, projections, CSV imports, and audit semantics.

See [release verification](docs/verification.md) for acceptance-test coverage, browser/PDF checks, and the remaining Linux host checks.

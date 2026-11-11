# Hoard Counter

A private household ledger for budgeting, spending, subscriptions, investment planning, and financial reports. Next.js/React/TypeScript frontend, Flask backend, and SQLite storage. Runs with Docker Compose and is accessible locally without Tailscale or another external service.

## What is included

- Individual household logins with administrator/member permissions.
- Exact-decimal, double-entry transactions, editable history, refunds, native currencies, dated USD conversion rates, and asset/debt balances.
- Receipts and item images, categories/tags, recurring subscriptions, transaction rules, and monthly budgets.
- History-based expense forecasts, aggregate investment valuations, cash-flow-adjusted performance, and saved projection scenarios.
- Filtered CSV/PDF reports, editable CSV templates, previewed UUID/timestamp imports, and complete financial archives with attachments.
- Persistent Docker storage, Alembic migrations, verified weekly backups with calendar retention, and an upgrade/restore command.

## Run on Linux

Docker Engine and Docker Compose v2 are sufficient to build and run the application. See [the operations guide](docs/operations.md) for optional remote access, scheduled backups, releases, and recovery. Docker images never contain the household database or uploads.

```sh
cp .env.example .env
docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(32))'
# Set SECRET_KEY in .env to the generated value.
docker compose up --build -d --wait
```

Open [http://localhost:8080](http://localhost:8080) and create the first administrator. The one-shot `init` container prepares persistent storage and the initial database before the API starts. No host Python, Node.js, or VPN is needed. Start by adding financial accounts and opening balances. Built-in Income and Expenses counterparties are provided; add named payees and income sources as needed. For non-USD accounts, enter exchange rates before transactions or nonzero opening balances.

The default binding is local-only. Configure `BIND_ADDRESS` and `HTTP_PORT` in `.env` for another interface or port. Keep `COOKIE_SECURE=false` for direct HTTP; set it to `true` when using an optional HTTPS reverse proxy. Tailscale may provide host-level remote access, but is not part of the application. Existing `.env` files keep their values: change a previous `COOKIE_SECURE=true` to `false` before switching to local HTTP.

Use the [managed upgrade procedure](docs/operations.md#releases-and-rollback) for new releases so migrations have a verified backup and rollback path. Plain Compose startup refuses to migrate an existing installation to a different release.

## Local development

Python 3.11+ and Node.js 22+ are required. Use disposable development data and a different secret from production.

Start in the repository root and use the commands for your shell. Keep separate virtual environments if you switch between Windows and WSL: a Linux virtual environment cannot run with Windows Python.

### Windows PowerShell

These commands run the virtual environment's Python directly, so no activation script is needed.

```powershell
py -3 -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:DATA_DIR = Join-Path $PWD 'data'
$env:UPLOAD_DIR = Join-Path $PWD 'uploads'
$env:BACKUP_DIR = Join-Path $PWD 'backups'
$env:SECRET_KEY = .\.venv-windows\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
$env:COOKIE_SECURE = 'false'
cd backend
..\.venv-windows\Scripts\python.exe -m alembic upgrade head
..\.venv-windows\Scripts\python.exe -m flask --app whc:create_app run --host 127.0.0.1 --port 5000
```

### Windows Command Prompt

```bat
py -3 -m venv .venv-windows
.venv-windows\Scripts\activate.bat
python -m pip install -r backend\requirements.txt
set "DATA_DIR=%CD%\data"
set "UPLOAD_DIR=%CD%\uploads"
set "BACKUP_DIR=%CD%\backups"
for /f %K in ('python -c "import secrets; print(secrets.token_hex(32))"') do set "SECRET_KEY=%K"
set "COOKIE_SECURE=false"
cd backend
python -m alembic upgrade head
python -m flask --app whc:create_app run --host 127.0.0.1 --port 5000
```

If the Windows Python launcher (`py`) is unavailable but `python --version` reports Python 3.11 or newer, use `python -m venv .venv-windows` instead.

### Linux, macOS, or WSL

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

### Frontend

Open a second terminal at the repository root. These commands work in Windows Command Prompt, PowerShell, Linux, macOS, and WSL.

```sh
cd frontend
npm exec --yes -- pnpm@11.23.0 install --frozen-lockfile
npm exec --yes -- pnpm@11.23.0 dev
```

If PowerShell blocks `npm.ps1`, use `npm.cmd` in place of `npm` in the frontend commands.

Open `http://127.0.0.1:3000`. The development frontend forwards API requests to Flask. Financial state stays in the directories above across frontend/backend restarts. The cookie setting follows the access protocol: `COOKIE_SECURE=false` for HTTP, `true` for HTTPS.

## Validation and API

From the repository root on Windows:

```bat
.\.venv-windows\Scripts\python.exe -m pytest backend/tests -q
.\.venv-windows\Scripts\python.exe backend/tools/generate_contract.py
```

On Linux, macOS, or WSL:

```sh
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python backend/tools/generate_contract.py
```

To validate the frontend from the repository root:

```sh
cd frontend
npm exec --yes -- pnpm@11.23.0 test
npm exec --yes -- pnpm@11.23.0 typecheck
npm exec --yes -- pnpm@11.23.0 build
```

The committed [OpenAPI contract](openapi.json) is served at `/api/v1/openapi.json` after login. Its TypeScript interfaces are generated into `frontend/lib/generated.ts`. Money travels as decimal strings; mutations require `X-CSRF-Token`, and edits/voids require the revision the caller last read.

Read [accounting and exchange behavior](docs/accounting.md) for currencies, returns, projections, CSV imports, and audit semantics.

See [release verification](docs/verification.md) for acceptance-test coverage, browser/PDF checks, and the remaining Linux host checks.

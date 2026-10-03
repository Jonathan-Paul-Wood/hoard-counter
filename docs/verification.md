# Release verification

## Automated coverage

Run `.venv/bin/python -m pytest backend/tests -q` from the repository root. The suite uses isolated temporary databases and upload directories.

| Area | Evidence |
| --- | --- |
| Ledger, currencies, revisions, refunds and permissions | `backend/tests/test_core.py`, `test_admin_auth.py` |
| Subscription recurrence, rule order and occurrence matching | `backend/tests/test_core.py` |
| Forecast coverage, recurring exclusions, investment flows and valuation replay | `backend/tests/test_analytics_edges.py`, `test_analytics_exchange.py` |
| UUID conflicts, tombstones, attachments and archive round trips | `backend/tests/test_exchange_edges.py`, `test_analytics_exchange.py` |
| Screen/report calculations, signed refund CSVs, all CSV sections and multipage PDF output | `backend/tests/test_analytics_exchange.py` |
| Live SQLite snapshots, hashes, fresh-directory restores, calendar retention and failed migration recovery | `backend/tests/test_backup.py` |
| OpenAPI route coverage, schema references and generated TypeScript parity | `backend/tests/test_contract.py` |
| Docker bootstrap/restart preservation, refusal of unsafe migrations, direct HTTP login/CSRF, and optional Secure cookies | `backend/tests/test_docker_startup.py` |

Run `pnpm test`, `pnpm typecheck`, and `pnpm build` from `frontend/`. Formatter tests cover exact decimal rounding, large values, negative balances, cryptocurrency precision, and exact aggregation. The production build includes a standalone server and static assets.

## Checks performed during implementation

- Opened the actual application in a browser at desktop and mobile widths. Created an account with a $2,500 opening balance, recorded an expense, edited it to $60, and confirmed the $2,440 balance across views.
- Submitted an invalid USD amount with three decimal places and verified the error toast, persistent error, and retained editor contents.
- Verified mobile navigation, visible period selection, contained table scrolling, and report CSV section selection.
- Exercised 37 HTTP operations through the built Next.js frontend proxy against Flask, including authenticated resources, each CSV report section, PDF output, archive export, and readiness.
- Rendered and inspected a 13-page report containing charts, long descriptions, repeated table headers, budgets, audit records, and page numbers.
- Applied the initial Alembic migration repeatedly and verified existing records survived.

All interactive check data is disposable and stored under ignored `tmp/` directories. Production startup does not seed sample accounts or transactions.

## Destination-host validation

This development machine did not have Docker. Container builds, actual Linux container readiness, host cron execution, and real image rollback have not been run here. The failed-migration test restores real database/upload files while substituting the Docker commands. Optional VPN/HTTPS access is separate from Docker-only acceptance.

Follow [the operations guide](operations.md) on the Linux host. Before using the installation for household records:

1. With no Tailscale or external proxy running, start a fresh installation using `docker compose up --build -d --wait`, open `http://localhost:8080`, and create a temporary account, transaction, and receipt. Confirm login persists and the CSRF-protected save succeeds. Verify `init` exited successfully.
2. Create and verify a backup, recreate the containers, and confirm the same balance and authenticated attachment remain available.
3. Restore that archive into the separate rehearsal directory described in the guide and check its database and receipt.
4. Deploy a new immutable version, verify readiness and retained records, then exercise migration-failure recovery in an isolated installation.
5. Confirm the installed cron entry creates a verified backup in the configured household timezone and that Settings reports its successful timestamp.

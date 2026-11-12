# Linux operations

Hoard Counter runs as three non-root Docker Compose services: Next.js, Flask/Gunicorn, and an nginx proxy. A one-shot initialization container prepares storage before startup. The default entry point is `http://localhost:8080`, published on `127.0.0.1:8080`. No VPN or external proxy is required. The SQLite database and uploads live outside the source checkout and container images.

## First installation

Runtime requirements: a Linux machine with Docker Engine and Compose v2 supporting `up --wait`. Host Python and Node.js are not required for Docker startup. Python 3.11+ is used only by the optional host deployment/restore manager, and cron by scheduled backups. Use a local filesystem for SQLite, not an NFS/SMB-mounted directory. These commands assume the checkout is `/opt/hoard-counter`.

```sh
cd /opt/hoard-counter
cp .env.example .env
docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(32))'
```

Put the generated value in `.env` as `SECRET_KEY`, set `HOUSEHOLD_TIMEZONE`, and retain `COOKIE_SECURE=false` for default local HTTP access. Use simple `KEY=value` configuration lines; deployment tools do not execute the file as shell code. Keep this secret stable between releases and retain a separate protected copy of `.env` for machine recovery. The backup archives do not contain secrets.

```sh
chmod 600 .env
docker compose up --build -d --wait
docker compose ps --all
```

Open `http://localhost:8080` on the Docker host and complete the first-user setup. The `init` service should show `Exited (0)`; the three application services should be running. On initial startup, `init` creates the directories below, gives the application UID/GID `10001` access, and runs Alembic as that user. It records the installed release so the deployment manager can later upgrade it with a backup. The short-lived initializer needs container root to prepare bind-mount permissions; the serving application containers remain non-root and none needs the Docker socket.

Persistent locations:

| Host location | Contents |
| --- | --- |
| `/srv/hoard-counter/data/hoardcounter.db` | SQLite database; its live WAL/SHM files stay in the same directory |
| `/srv/hoard-counter/uploads/` | Receipt and item-image files |
| `/srv/hoard-counter/backups/` | Verified full backup archives |
| `/srv/hoard-counter/release.json` | Active release and matched rollback archive |

Set `WHC_DATA_ROOT` to change the common storage directory. Keep it outside the checkout. Docker startup preserves existing records, uploads, and release metadata. An existing untracked database, a mismatched schema/release, or active maintenance makes initialization fail instead of silently migrating or overwriting data. Inspect failures with `docker compose logs init api`.

If you prefer the host manager for first installation, use `sudo python3 scripts/manage.py deploy --version 1.0.0` instead of the Compose startup command. It prepares the same storage and release metadata. It needs permission to manage these directories and run Docker; the examples use `sudo`.

## Local and optional remote access

| Setting | Default | Purpose |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface published by Docker |
| `HTTP_PORT` | `8080` | Host port for the application |
| `COOKIE_SECURE` | `false` | Use `false` for HTTP and `true` when accessed over HTTPS |

For LAN access, set `BIND_ADDRESS` to the host's LAN IP, or `0.0.0.0` to listen on all IPv4 interfaces, then use `http://HOST-IP:8080`. Limit access to the intended household network. Only nginx publishes a port; Flask and Next.js remain internal to Docker.

An existing Tailscale installation can be used just like another network interface: bind to the machine's Tailscale IP and open `http://TAILSCALE-IP:8080`. Alternatively, an SSH tunnel can reach the default loopback binding. Neither option requires changes to the application or a Tailscale container.

HTTPS is optional and independent of the VPN choice. An HTTPS reverse proxy may forward to `http://127.0.0.1:8080`; preserve the public `Host` (including a nonstandard port), set `X-Forwarded-Proto: https`, and set `COOKIE_SECURE=true`. Keep the backend binding restricted to the proxy's access path. The bundled nginx also supports ordinary direct HTTP requests without forwarded headers.

After editing access settings for the same installed release, run `docker compose up -d --wait`. When installing this updated application over an older version, first use the managed upgrade below so the new initializer is present in the image. Existing `.env` files are not overwritten by upgrades. If changing from the previous HTTPS/Tailscale setup to HTTP, explicitly set `COOKIE_SECURE=false`; clear cookies for the hostname or sign in through a fresh browser session if an old Secure cookie remains. With `COOKIE_SECURE=true`, use the HTTPS address to sign in.

## Releases and rollback

Every release must use a new immutable tag. Keep the previous images available until the next successful release. Use the host manager (Python 3.11+) for upgrades; do not change `APP_VERSION` and run plain Compose against an older installed release. The initializer deliberately refuses that path. On the Linux host, update the checkout to the desired revision, then run:

```sh
sudo python3 scripts/manage.py deploy --version 1.1.0
```

The command builds while the previous version continues serving. It then enters a brief maintenance window, creates and verifies a complete pre-upgrade snapshot, stops the old API, runs `alembic upgrade head` from the new image, and waits for both new services to become healthy. Only then does it update the active release and reopen access. The Compose API checks that the schema is current on startup and leaves upgrades to the deployment manager so they always have a recovery path.

After a successful managed upgrade or restore, set `APP_VERSION` in `.env` to the active version shown by `scripts/manage.py status` before running direct Compose commands. The manager uses the explicit release tag and does not rewrite `.env`.

If migration or readiness fails, the command restores the verified snapshot into fresh directories, switches the stopped data/uploads directories, and restarts the previous images. A failed command still exits nonzero after successful rollback so deployment monitoring sees the failed upgrade. If rollback itself fails, the maintenance page remains and the exception identifies the recovery snapshot. Old stopped directories are retained as `.replaced-<id>` under the storage root; inspect and remove those manually once recovery is confirmed.

Do not use a mutable `latest` tag or rebuild an existing release tag. The local-build path rejects existing tags. For a registry workflow, set `IMAGE_PREFIX`, for example `ghcr.io/your-name/hoard-counter`, on the builder and server:

```sh
# On the builder, with a configured .env and a new immutable tag:
APP_VERSION=1.1.0 docker compose build --pull api web
APP_VERSION=1.1.0 docker compose push api web

# On the server, after updating the deployment checkout/config:
sudo python3 scripts/manage.py deploy --version 1.1.0 --pull
```

Ordinary rebuilds/recreates leave the bind-mounted records intact. Never delete the host storage directories when replacing images. Do not run a migration manually against a live API. Retain adequate free disk space for a database table rebuild, the full snapshot, and backup staging.

### Adding database migrations

From `backend/`, run `python -m alembic revision -m "describe the change"`. Commit the script in `backend/migrations/versions/` as `yyyy-mm-dd-hh-mm-ssss-short-slug.py`. Alembic generates a UTC timestamp (the final four digits are seconds and hundredths, defaulting to `00` hundredths); change any underscores in the generated slug to hyphens. Implement `upgrade()` using Alembic operations and keep it inside the database transaction. Avoid explicit commits, autocommit operations, and changes to files or external services.

Keep one Alembic revision chain. If concurrent changes add migrations from the same parent, resolve the branch before shipping. Revision dependencies control execution order; a timestamp by itself cannot tell whether a migration ran. Keep applied scripts and their filenames unchanged, and correct a shipped migration with a new revision. To roll back a release, restore the matching database backup and application version.

The `"migration-runs"` table records each successful upgrade with its UUID, filename, filename timestamp, and UTC execution time. The original `0001_initial` revision predates the audit table and is tracked only in Alembic's history. For SQLite, the pending migrations, revision updates, and audit inserts commit together. If a migration fails, startup stops and the transaction rolls back; fix the failure before restarting.

## Weekly backup schedule

Install this file as `/etc/cron.d/hoard-counter`, adjusting the checkout path if needed:

```cron
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/15 * * * * root /usr/bin/python3 /opt/hoard-counter/scripts/manage.py backup --scheduled >> /var/log/hoard-counter-backup.log 2>&1
```

Cron checks every fifteen minutes, but the command creates at most one successful scheduled backup each Sunday after 03:00 in the household timezone saved in Settings, falling back to `HOUSEHOLD_TIMEZONE` before settings are saved. The backup CLI uses that same timezone for retention. This avoids dependence on the host cron implementation's `CRON_TZ` support. Failures retry later that Sunday; if the host is down all Sunday, the next scheduled opportunity is the following Sunday. Run the command without `--scheduled` for an immediate backup:

```sh
sudo python3 /opt/hoard-counter/scripts/manage.py backup
```

The application and backup command use a shared filesystem mutation lock. Backup staging briefly blocks changes while SQLite's backup API creates a consistent database snapshot and copies the uploads. Reads remain available. Compression and verification run after that lock is released. The archive contains a SQLite snapshot, all uploaded files, and a manifest with schema/application versions, UTC timestamp, file sizes, and SHA-256 hashes. Verification checks SQLite integrity, foreign keys, and each file hash before atomic publication. An interrupted or failed backup never replaces a completed archive and never triggers pruning.

The Settings backup status reads `data/backup-status.json`, including last success and last error. Scheduled runs, manual backup runs, restores, and deployments share a host operations lock. A conflicting operation fails without changing archives. Rotate the cron log through the host's normal logrotate configuration.

Retention uses the household timezone and one union of these rules:

- Latest successful snapshot in the current ISO week and each of the previous three ISO weeks.
- Latest successful snapshot in each of the previous three completed calendar months.
- Latest successful snapshot in each of the previous three completed calendar quarters.
- Latest successful snapshot in every completed calendar year, retained indefinitely.
- Latest current-year snapshot as the future annual candidate, and always the latest successful snapshot overall.

Missing periods contribute no snapshot. One physical archive can satisfy several rules; the annual candidate normally overlaps weekly retention. The union is computed before any deletion, so the final weekly snapshot of a year becomes its annual recovery point safely. Calendar-period snapshots are the last available weekly snapshots, not guaranteed midnight month/year-end states.

Manual/pre-upgrade snapshots are excluded from scheduled pruning. Deployment keeps the preceding successful upgrade's recovery snapshot until the next upgrade succeeds. Standalone manual recovery checkpoints are retained until an operator removes them. Backups are local-only as configured; indefinite annual retention requires increasing disk capacity over time.

## Verify or restore

Use the active release from `scripts/manage.py status` in direct Compose commands. For example:

```sh
APP_VERSION=1.1.0 docker compose run --rm --no-deps api \
  python -m whc.backup verify /backups/ARCHIVE.tar.gz

APP_VERSION=1.1.0 docker compose run --rm --no-deps api \
  python -m whc.backup prune --dry-run
```

Restore is explicit and replaces the active records after verification. Keep the chosen archive directly inside the configured backups directory:

```sh
sudo python3 scripts/manage.py restore /srv/hoard-counter/backups/ARCHIVE.tar.gz
```

The command fully verifies/extracts the archive into a fresh temporary directory before stopping the application. It makes a recovery backup of the existing installation, enters maintenance, swaps stopped directories, and starts the image version recorded in the archive. It rejects an existing target directory in the underlying restore API. It does not attempt a schema downgrade. If the matching images are no longer present, restore those immutable images from your registry first. On a fresh machine with no `release.json`, specify the matching image version explicitly:

```sh
sudo python3 scripts/manage.py restore /srv/hoard-counter/backups/ARCHIVE.tar.gz --version 1.1.0
```

For a nondestructive recovery rehearsal, mount a separate empty host directory and use the low-level command. The `restored` target must not already exist, and the parent must be writable by UID 10001:

```sh
sudo install -d -o 10001 -g 10001 -m 750 /srv/hoard-restore-check
APP_VERSION=1.1.0 docker compose run --rm --no-deps \
  -v /srv/hoard-restore-check:/restore api \
  python -m whc.backup restore /backups/ARCHIVE.tar.gz --target /restore/restored
```

The low-level restore creates `restored/data/hoardcounter.db`, `restored/uploads/`, and `restored/manifest.json`; it never alters the running installation. Run a rehearsal periodically and check account totals and attachments in an isolated app instance.

## Checks and current validation limits

Run backend tests with `python -m pytest` from `backend/`. Backup tests exercise live WAL snapshots, full restore, hashes, unsafe archive paths, calendar retention boundaries, verification failure, shared-lock release, and actual database/upload recovery after a simulated migration failure. The deployment manager uses Python argument arrays rather than shell-built commands.

Docker build, nginx execution, real container readiness, local HTTP login, host reboot, and actual Linux cron must be checked on the destination host. If you configure remote access or HTTPS, verify that separately. The development environment used for initial implementation did not have a Docker daemon; mocked deployment failure tests do not replace that host smoke test.

Before relying on the installation: create a test account and transaction with an attachment, make a backup, recreate the containers, confirm the data remains, and restore the archive into the separate rehearsal directory. For a subsequent release, verify one migration and its tested recovery path before entering important household records.

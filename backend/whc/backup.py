"""Verified SQLite/attachment backups and calendar retention (stdlib only).

Run ``python -m whc.backup --help``. No Flask app is created by this module.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile
import time
from uuid import uuid4
from zoneinfo import ZoneInfo


@contextmanager
def mutation_lock(data_dir, exclusive=False):
    """Shared for application writes; exclusive while staging a backup.

    Linux flock works across processes and releases automatically on exit.
    The Windows development fallback serializes both lock types.
    """
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".mutation.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def database_path():
    value = os.environ.get("DATABASE_URL", "")
    if value:
        if not value.startswith("sqlite:///") or "?" in value:
            raise ValueError("Backups require a local sqlite:/// DATABASE_URL without query parameters")
        return Path(value[len("sqlite:///"):])
    return Path(os.environ.get("DATA_DIR", "/data")) / "hoardcounter.db"


def configured_timezone():
    value = os.environ.get("HOUSEHOLD_TIMEZONE", "America/New_York")
    database = database_path()
    if database.is_file():
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                row = conn.execute("SELECT data FROM records WHERE kind='setting' AND deleted=0 LIMIT 1").fetchone()
            if row:
                value = json.loads(row[0]).get("timezone", value)
        except sqlite3.OperationalError:
            pass  # Bootstrap databases may not have the records table yet.
    ZoneInfo(value)
    return value


def _utc(value=None):
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Backup timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise ValueError("SQLite integrity check failed: " + repr(integrity[:3]))
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite foreign key check failed")
        try:
            revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            return revision[0] if revision else None
        except sqlite3.OperationalError:
            return None


def _upload_files(root):
    if not root.exists():
        return
    if root.is_symlink():
        raise ValueError("Upload root must not be a symlink")
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(folder) / name
            if path.is_symlink():
                raise ValueError("Symlinks are not allowed in uploads: " + str(path))
        for name in sorted(files):
            path = Path(folder) / name
            if not path.is_file():
                raise ValueError("Only regular upload files can be backed up")
            yield path


def create_backup(database, uploads, destination, *, data_dir=None, now=None,
                  household_timezone="America/New_York", purpose="scheduled"):
    """Stage DB and files under one mutation gate, verify, then publish atomically."""
    database, uploads, destination = map(Path, (database, uploads, destination))
    if not database.is_file():
        raise FileNotFoundError("Database does not exist: " + str(database))
    ZoneInfo(household_timezone)
    if purpose not in ("scheduled", "pre-upgrade", "manual"):
        raise ValueError("Unknown backup purpose")
    moment = _utc(now)
    destination.mkdir(parents=True, exist_ok=True)
    ident = str(uuid4())
    name = moment.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + ident + ".tar.gz"
    archive = destination / name
    temporary_archive = destination / ("." + name + ".partial")
    try:
        with tempfile.TemporaryDirectory(prefix=".backup-stage-", dir=destination) as tmp:
            stage = Path(tmp)
            with mutation_lock(data_dir or database.parent, exclusive=True):
                snapshot = stage / "database.sqlite3"
                with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(snapshot)) as target:
                        source.backup(target, pages=256)
                        target.execute("PRAGMA journal_mode=DELETE")
                for item in _upload_files(uploads):
                    target = stage / "uploads" / item.relative_to(uploads)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, target)
            revision = _check_database(snapshot)
            files = {}
            for item in sorted(stage.rglob("*")):
                if item.is_file():
                    files[item.relative_to(stage).as_posix()] = {
                        "sha256": _hash(item), "size": item.stat().st_size,
                    }
            manifest = {
                "format_version": 1, "id": ident,
                "created_at": moment.isoformat(), "household_timezone": household_timezone,
                "application_version": os.environ.get("APP_VERSION", "development"),
                "schema_revision": revision, "purpose": purpose, "files": files,
            }
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            with tarfile.open(temporary_archive, "w:gz") as tar:
                for relative in ["manifest.json", *files]:
                    tar.add(stage / relative, arcname=relative, recursive=False)
            os.chmod(temporary_archive, 0o600)
            verify_backup(temporary_archive)
            with temporary_archive.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary_archive, archive)
            _fsync_directory(destination)
        return archive
    finally:
        temporary_archive.unlink(missing_ok=True)


def _fsync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _safe_name(name):
    parts = PurePosixPath(name)
    if not name or "\\" in name or parts.is_absolute() or ".." in parts.parts or ":" in name:
        raise ValueError("Unsafe path in backup: " + name)
    if parts.as_posix() != name:
        raise ValueError("Noncanonical path in backup: " + name)
    return name


def _manifest_and_members(tar):
    members = {}
    for member in tar.getmembers():
        name = _safe_name(member.name)
        if not member.isfile() or name in members:
            raise ValueError("Backup contains duplicate paths or nonregular files")
        members[name] = member
    if "manifest.json" not in members or members["manifest.json"].size > 16 * 1024 * 1024:
        raise ValueError("Missing or oversized backup manifest")
    manifest = json.load(tar.extractfile(members["manifest.json"]))
    if manifest.get("format_version") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported backup format")
    _utc(datetime.fromisoformat(manifest["created_at"]))
    files = manifest["files"]
    if "database.sqlite3" not in files or set(members) != {"manifest.json", *files}:
        raise ValueError("Backup file list does not match its manifest")
    for name, info in files.items():
        _safe_name(name)
        if name != "database.sqlite3" and not name.startswith("uploads/"):
            raise ValueError("Unexpected backup file: " + name)
        if not isinstance(info, dict) or info.get("size") != members[name].size:
            raise ValueError("Backup file size mismatch: " + name)
    return manifest, members


def _unpack_verified(archive, target):
    with tarfile.open(archive, "r:gz") as tar:
        manifest, members = _manifest_and_members(tar)
        for name, info in manifest["files"].items():
            output = target / name
            output.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with tar.extractfile(members[name]) as source, output.open("xb") as dest:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    dest.write(chunk)
                    digest.update(chunk)
            os.chmod(output, 0o600)
            if digest.hexdigest() != info.get("sha256"):
                raise ValueError("Backup checksum mismatch: " + name)
        _check_database(target / "database.sqlite3")
        return manifest


def verify_backup(archive):
    # Use the backup disk, not a container's size-limited /tmp tmpfs.
    with tempfile.TemporaryDirectory(prefix=".verify-", dir=Path(archive).resolve().parent) as tmp:
        return _unpack_verified(Path(archive), Path(tmp))


def restore_backup(archive, target):
    """Restore into a NEW directory; never overwrite the running database."""
    target = Path(target)
    if target.exists():
        raise ValueError("Restore target must not exist; restore into a fresh directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=target.parent))
    try:
        manifest = _unpack_verified(Path(archive), stage)
        (stage / "data").mkdir()
        (stage / "uploads").mkdir(exist_ok=True)
        (stage / "database.sqlite3").rename(stage / "data" / "hoardcounter.db")
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.rename(stage, target)
        _fsync_directory(target.parent)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def retention_keep(manifests, *, now=None, household_timezone="America/New_York"):
    """Return IDs to keep, using exact calendar windows, with annual candidates."""
    local = _utc(now).astimezone(ZoneInfo(household_timezone))
    current_week = local.date() - timedelta(days=local.weekday())
    week_ids = {current_week - timedelta(weeks=i) for i in range(4)}
    current_month = local.year * 12 + local.month - 1
    month_ids = {current_month - i for i in range(1, 4)}
    current_quarter = local.year * 4 + (local.month - 1) // 3
    quarter_ids = {current_quarter - i for i in range(1, 4)}
    representatives = {}
    keep = set()
    for item in manifests:
        if item.get("purpose", "scheduled") != "scheduled":
            keep.add(item["id"])
            continue
        stamp = _utc(datetime.fromisoformat(item["created_at"]))
        if stamp > _utc(now):
            keep.add(item["id"])
            continue
        when = stamp.astimezone(ZoneInfo(household_timezone))
        week = when.date() - timedelta(days=when.weekday())
        month = when.year * 12 + when.month - 1
        quarter = when.year * 4 + (when.month - 1) // 3
        groups = [("latest", 0)]
        if week in week_ids:
            groups.append(("week", str(week)))
        if month in month_ids:
            groups.append(("month", month))
        if quarter in quarter_ids:
            groups.append(("quarter", quarter))
        # Completed years are permanent; current year is the in-progress candidate.
        if when.year <= local.year:
            groups.append(("year", when.year))
        for key in groups:
            candidate = (stamp, item["id"])
            if key not in representatives or candidate > representatives[key]:
                representatives[key] = candidate
    keep.update(item[1] for item in representatives.values())
    return keep


def prune_backups(directory, *, now=None, household_timezone="America/New_York", dry_run=False):
    """Verify every candidate before computing one union and pruning. Fail closed."""
    entries = []
    for archive in sorted(Path(directory).glob("*.tar.gz")):
        entries.append((archive, verify_backup(archive)))
    keep = retention_keep([item for _, item in entries], now=now,
                          household_timezone=household_timezone)
    removed = []
    for archive, manifest in entries:
        if manifest["id"] not in keep:
            removed.append(str(archive))
            if not dry_run:
                archive.unlink()
    return removed


def write_status(data_dir, *, archive=None, error=None):
    """Small UI-readable status file, independent of the financial database."""
    directory = Path(data_dir)
    path = directory / "backup-status.json"
    try:
        state = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        state = {}
    moment = _utc().isoformat()
    state.update(status="error" if error else "ok", last_attempt=moment, last_error=error)
    if archive:
        state.update(last_success=moment, archive=Path(archive).name)
    temporary = directory / (".backup-status-" + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--purpose", choices=["scheduled", "pre-upgrade", "manual"], default="scheduled")
    create.add_argument("--prune", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("archive")
    restore = sub.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--target", required=True)
    prune = sub.add_parser("prune")
    prune.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    tz = configured_timezone()
    backups = Path(os.environ.get("BACKUP_DIR", "/backups"))
    if args.command == "create":
        status_dir = Path(os.environ.get("DATA_DIR", str(database_path().parent)))
        try:
            path = create_backup(database_path(), os.environ.get("UPLOAD_DIR", "/uploads"), backups,
                                 data_dir=status_dir, household_timezone=tz, purpose=args.purpose)
            write_status(status_dir, archive=path)
            # Publication and pruning stay in that order. Caller serializes scheduled jobs.
            if args.prune:
                prune_backups(backups, household_timezone=tz)
        except Exception as error:
            if status_dir.exists():
                write_status(status_dir, error=str(error))
            raise
        print(path)
    elif args.command == "verify":
        print(json.dumps(verify_backup(args.archive), indent=2))
    elif args.command == "restore":
        print(json.dumps(restore_backup(args.archive, args.target), indent=2))
    else:
        print(json.dumps(prune_backups(backups, household_timezone=tz, dry_run=args.dry_run), indent=2))


if __name__ == "__main__":
    main()

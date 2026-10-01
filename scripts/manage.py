#!/usr/bin/env python3
"""Linux deployment/backup/restore orchestration. Run from a trusted checkout."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]
UID = 10001


def settings():
    env = dict(os.environ)
    config = PROJECT / ".env"
    if not config.is_file():
        raise RuntimeError("Copy .env.example to .env and configure SECRET_KEY first")
    for line in config.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, sep, value = line.partition("=")
            if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                raise RuntimeError("Use simple KEY=value lines in .env")
            env[key.strip()] = value.strip().strip("\"'")
    if len(env.get("SECRET_KEY", "")) < 32 or "replace-with" in env.get("SECRET_KEY", ""):
        raise RuntimeError("SECRET_KEY must be a random secret of at least 32 characters")
    return env


def storage(env):
    raw = Path(env.get("WHC_DATA_ROOT", "/srv/hoard-counter"))
    if not raw.is_absolute() or raw.is_symlink():
        raise RuntimeError("WHC_DATA_ROOT must be an absolute directory, not a symlink")
    root = raw.resolve()
    if len(root.parts) < 3 or root in (PROJECT, PROJECT.parent):
        raise RuntimeError("Use a dedicated persistent storage directory outside the checkout")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("data", "uploads", "backups"):
        path = root / name
        if path.is_symlink():
            raise RuntimeError("Persistent directories must not be symlinks")
        path.mkdir(exist_ok=True)
        if os.geteuid() == 0:
            os.chown(path, UID, UID)
        path.chmod(0o750 if name != "data" else 0o755)
    return root


@contextmanager
def operations_lock(root):
    import fcntl
    with (root / ".operations.lock").open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another backup, restore, or deployment is already running") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def version(value):
    if not value or value in ("latest", "development") or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", value):
        raise RuntimeError("Use a unique immutable release tag, such as 1.0.0 or a git commit hash")
    return value


def compose(env, release, *args, capture=False):
    run_env = dict(env, APP_VERSION=version(release))
    command = ["docker", "compose", "--project-directory", str(PROJECT),
               "--env-file", str(PROJECT / ".env"), "-f", str(PROJECT / "compose.yaml"), *args]
    result = subprocess.run(command, env=run_env, cwd=PROJECT, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def read_release(root):
    path = root / "release.json"
    return json.loads(path.read_text()) if path.exists() else None


def household_timezone(env, root):
    value = env.get("HOUSEHOLD_TIMEZONE", "America/New_York")
    database = root / "data" / "hoardcounter.db"
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT data FROM records WHERE kind='setting' AND deleted=0 LIMIT 1").fetchone()
        if row:
            value = json.loads(row[0]).get("timezone", value)
    return ZoneInfo(value)


def write_release(root, value):
    temporary = root / ".release.json.partial"
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, root / "release.json")


def backup(env, root, release, purpose="scheduled", prune=False):
    args = ["run", "--rm", "--no-deps", "api", "python", "-m", "whc.backup", "create", "--purpose", purpose]
    if prune:
        args.append("--prune")
    output = compose(env, release, *args, capture=True)
    name = Path(output.splitlines()[-1]).name
    path = root / "backups" / name
    if not path.is_file() or not name.endswith(".tar.gz"):
        raise RuntimeError("Backup command did not return a published archive")
    return path


def maintenance(root, enabled):
    path = root / "data" / ".maintenance"
    if enabled:
        path.write_text("Hoard Counter is undergoing maintenance.\n")
        path.chmod(0o644)
    else:
        path.unlink(missing_ok=True)


def stop_apps(env, release):
    compose(env, release, "stop", "--timeout", "130", "api", "web")


def start_apps(env, release, recreate_proxy=False):
    compose(env, release, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "180", "api", "web")
    # Recreate also remounts /state after an atomic data-directory restore.
    args = ["up", "-d", "--no-deps"]
    if recreate_proxy:
        args.append("--force-recreate")
    compose(env, release, *args, "proxy")


def stage_restore(env, root, release, archive):
    archive = Path(archive).resolve()
    if archive.parent != (root / "backups").resolve() or not archive.is_file():
        raise RuntimeError("Choose an archive directly inside the configured backups directory")
    stage = root / (".restore-" + uuid4().hex)
    stage.mkdir(mode=0o750)
    if os.geteuid() == 0:
        os.chown(stage, UID, UID)
    try:
        compose(env, release, "run", "--rm", "--no-deps", "-v", str(stage) + ":/restore", "api",
                "python", "-m", "whc.backup", "restore", "/backups/" + archive.name,
                "--target", "/restore/restored", capture=True)
        return stage
    except BaseException:
        shutil.rmtree(stage)
        raise


def swap_restored(root, stage):
    """API must be stopped. Old state stays in quarantine for manual recovery."""
    restored = stage / "restored"
    for name in ("data", "uploads"):
        if not (restored / name).is_dir() or (restored / name).is_symlink():
            raise RuntimeError("Invalid restored directory")
    (restored / "data" / ".maintenance").write_text("Restore maintenance\n")
    (restored / "data").chmod(0o755)
    quarantine = root / (".replaced-" + uuid4().hex)
    quarantine.mkdir(mode=0o700)
    moved = []
    try:
        for name in ("data", "uploads"):
            (root / name).rename(quarantine / name)
            moved.append((quarantine / name, root / name))
            (restored / name).rename(root / name)
            moved.append((root / name, restored / name))
    except BaseException:
        for source, destination in reversed(moved):
            source.rename(destination)
        raise
    print("Previous stopped state retained at " + str(quarantine), file=sys.stderr)
    shutil.rmtree(stage)
    return quarantine


def deploy(env, root, release, pull=False):
    release = version(release)
    old = read_release(root)
    if old and old["version"] == release:
        raise RuntimeError("That version is already deployed; create a new immutable release tag")
    if not old and (root / "data" / "hoardcounter.db").exists():
        raise RuntimeError("Existing untracked database: use restore with its matching release instead of bootstrap")
    if (root / "data" / ".maintenance").exists():
        raise RuntimeError("Maintenance is already active; investigate the previous operation first")
    # Build/pull while the current version continues serving requests.
    if pull:
        compose(env, release, "pull", "api", "web")
    else:
        prefix = env.get("IMAGE_PREFIX", "hoard-counter")
        for component in ("api", "web"):
            found = subprocess.run(["docker", "image", "inspect", prefix + "-" + component + ":" + release],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if found.returncode == 0:
                raise RuntimeError("Release image already exists; use a new tag or --pull for a published release")
        compose(env, release, "build", "--pull", "api", "web")
    checkpoint = None
    disrupted = False
    maintenance(root, True)
    try:
        if old:
            checkpoint = backup(env, root, old["version"], purpose="pre-upgrade")
        # The sentinel and backup gate drain in-flight writes before this stop.
        disrupted = True
        stop_apps(env, old["version"] if old else release)
        compose(env, release, "run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head")
        start_apps(env, release)
        state = {"version": release, "previous_version": old["version"] if old else None,
                 "rollback_backup": checkpoint.name if checkpoint else None}
        write_release(root, state)
        maintenance(root, False)
    except BaseException:
        if not disrupted:
            maintenance(root, False)
        elif old and checkpoint:
            print("Upgrade failed. Restoring the matching previous image and snapshot.", file=sys.stderr)
            try:
                stop_apps(env, release)
                staged = stage_restore(env, root, release, checkpoint)
                swap_restored(root, staged)
                start_apps(env, old["version"], recreate_proxy=True)
                write_release(root, old)
                maintenance(root, False)
            except BaseException as rollback_error:
                maintenance(root, True)
                raise RuntimeError("Automatic rollback failed; maintenance remains active. Snapshot: " + str(checkpoint)) from rollback_error
        raise
    # The next successful upgrade replaces the prior pre-upgrade recovery anchor.
    prior = old.get("rollback_backup") if old else None
    if prior and Path(prior).name == prior and prior != state["rollback_backup"]:
        (root / "backups" / prior).unlink(missing_ok=True)
    print("Deployed " + release)


def restore(env, root, archive, release=None):
    old = read_release(root)
    if not old:
        if not release:
            raise RuntimeError("Fresh-machine restore requires --version matching the archive application version")
        reader_version = version(release)
    else:
        reader_version = old["version"]
    # Validation/extraction finishes before stopping a running application.
    staged = stage_restore(env, root, reader_version, archive)
    manifest = json.loads((staged / "restored" / "manifest.json").read_text())
    target_version = version(release or manifest["application_version"])
    if release and release != manifest["application_version"]:
        shutil.rmtree(staged)
        raise RuntimeError("Restore version must match the archive application_version")
    checkpoint = None
    try:
        maintenance(root, True)
        if old and (root / "data" / "hoardcounter.db").exists():
            checkpoint = backup(env, root, old["version"], purpose="manual")
        stop_apps(env, old["version"] if old else target_version)
        swap_restored(root, staged)
        start_apps(env, target_version, recreate_proxy=True)
        write_release(root, {"version": target_version, "previous_version": old["version"] if old else None,
                             "rollback_backup": checkpoint.name if checkpoint else None})
        maintenance(root, False)
    except BaseException:
        maintenance(root, True)
        print("Restore failed; maintenance remains active. Recovery backup: " + str(checkpoint), file=sys.stderr)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    print("Restored " + str(archive) + " using version " + target_version)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    deploy_parser = sub.add_parser("deploy")
    deploy_parser.add_argument("--version", required=True)
    deploy_parser.add_argument("--pull", action="store_true", help="Pull prebuilt release images instead of building")
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--scheduled", action="store_true",
                               help="Run at most once each Sunday after 03:00 in household timezone")
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("archive")
    restore_parser.add_argument("--version")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if os.name != "posix":
        parser.error("Deployment commands run on the Linux Docker host")
    env = settings()
    root = storage(env)
    with operations_lock(root):
        active = read_release(root)
        if args.command == "deploy":
            deploy(env, root, args.version, args.pull)
        elif args.command == "restore":
            restore(env, root, args.archive, args.version)
        elif args.command == "backup":
            if not active:
                raise RuntimeError("Deploy the application before scheduling backups")
            if (root / "data" / ".maintenance").exists():
                raise RuntimeError("Maintenance is active; backup job has not changed any archives")
            local = datetime.now(household_timezone(env, root))
            week = local.strftime("%G-W%V")
            marker = root / "scheduled-backup-week.txt"
            if args.scheduled:
                if local.weekday() != 6 or local.hour < 3:
                    return
                if marker.exists() and marker.read_text().strip() == week:
                    return
            print(backup(env, root, active["version"], prune=True))
            if args.scheduled:
                marker.write_text(week + "\n")
        else:
            print(json.dumps({"release": active, "maintenance": (root / "data" / ".maintenance").exists(),
                              "storage": str(root)}, indent=2))
            if active:
                compose(env, active["version"], "ps")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)

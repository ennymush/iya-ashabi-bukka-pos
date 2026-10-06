"""Backup, restore and automatic-backup service.

Safety rules:
  * The ACTIVE database is never deleted or overwritten by a normal backup.
  * A backup is validated (SQLite integrity check) before it is trusted.
  * Restoring ALWAYS takes a safety backup of the current database first, and
    the active database is swapped in atomically via a temporary file.
  * Retention keeps the newest N automatic backups and deletes older ones.
"""
from __future__ import annotations

import shutil
import sqlite3

import sqlalchemy
from datetime import datetime, timedelta
from pathlib import Path

from app.database.models import BackupLog, Setting, utcnow
from app.extensions import db
from app.utils.db_setup import integrity_check
from app.utils.logging_config import get_logger

log = get_logger("backup")

KIND_MANUAL = "manual"
KIND_AUTO = "auto"
KIND_PRE_RESTORE = "pre_restore"
KIND_RESTORE_SAFETY = "restore_safety"


class BackupError(Exception):
    """Raised with a message that is safe to show to the admin."""


def _database_path() -> Path:
    from flask import current_app

    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:///"):
        return Path(uri.replace("sqlite:///", "", 1))
    return None


def _dump_memory_database(target: Path) -> None:
    """Serialise an in-memory SQLite database into a real file.

    Used by the test-suite, where SQLALCHEMY_DATABASE_URI is ``sqlite://``.
    ``VACUUM INTO`` produces a consistent snapshot without touching the source.
    """
    db.session.commit()
    raw = db.session.connection().connection.driver_connection
    raw.execute("VACUUM INTO ?", (str(target),))


def backup_dir() -> Path:
    from flask import current_app

    directory = Path(current_app.config["BACKUP_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _unique_path(directory: Path, stem: str, suffix: str = ".db") -> Path:
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def _user_id(user) -> int | None:
    """Read a user's primary key without triggering a lazy refresh.

    ``getattr`` on a detached, expired instance raises DetachedInstanceError, so
    the value is pulled straight from the identity key instead.
    """
    if user is None:
        return None
    try:
        identity = sqlalchemy.inspect(user).identity
    except Exception:  # noqa: BLE001 - transient objects have no identity yet
        return None
    return identity[0] if identity else None


def create_backup(user=None, kind: str = KIND_MANUAL) -> BackupLog:
    """Use SQLite's backup API so the copy is always consistent, even while
    the till is mid-sale."""
    source = _database_path()
    in_memory = source is None
    if source is not None and not source.exists():
        raise BackupError("There is no database to back up yet.")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    kind_tag = "" if kind == KIND_MANUAL else f"_{kind}"
    target = _unique_path(backup_dir(), f"Iya_Ashabi_Bukka_Backup_{stamp}{kind_tag}")

    entry = BackupLog(filename=target.name, path=str(target), kind=kind,
                    user_id=_user_id(user))

    try:
        # WAL checkpoint so the main file is self-contained.
        try:
            if in_memory:
                _dump_memory_database(target)
            else:
                src = sqlite3.connect(str(source))
                try:
                    dst = sqlite3.connect(str(target))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
        except Exception:  # noqa: BLE001 - fall back to a plain copy
            if not in_memory:
                shutil.copy2(source, target)
            else:
                raise

        ok, detail = integrity_check(target)
        if not ok:
            target.unlink(missing_ok=True)
            entry.status = "FAILED"
            entry.message = f"Backup failed integrity check: {detail}"
            db.session.add(entry)
            db.session.commit()
            raise BackupError("The backup could not be verified, so it was discarded. Please try again.")

        entry.size_bytes = target.stat().st_size
        entry.status = "SUCCESS"
        entry.message = "Backup completed successfully."
        db.session.add(entry)
        db.session.commit()
        log.info("Backup created: %s (%s bytes)", target.name, entry.size_bytes)
    except BackupError:
        raise
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        target.unlink(missing_ok=True)
        entry.status = "FAILED"
        entry.message = str(exc)[:255]
        db.session.add(entry)
        db.session.commit()
        log.error("Backup failed: %s", exc)
        raise BackupError("The backup could not be completed. Please try again.")

    _apply_retention()
    return entry


def list_backups() -> list[BackupLog]:
    return BackupLog.query.order_by(BackupLog.created_at.desc()).limit(60).all()


def _apply_retention() -> int:
    """Keep only the newest N automatic backups."""
    retention = Setting.get_int("system.backup_retention", 30)
    if retention <= 0:
        return 0
    autos = (
        BackupLog.query.filter(BackupLog.kind == KIND_AUTO, BackupLog.status == "SUCCESS")
        .order_by(BackupLog.created_at.desc())
        .all()
    )
    removed = 0
    for entry in autos[retention:]:
        path = Path(entry.path) if entry.path else None
        if path and path.exists():
            try:
                path.unlink()
            except OSError as exc:
                log.warning("Could not delete old backup %s: %s", path.name, exc)
                continue
        db.session.delete(entry)
        removed += 1
    if removed:
        db.session.commit()
        log.info("Retention removed %d old backup(s)", removed)
    return removed


def validate_backup(path: Path) -> tuple[bool, str]:
    """Confirm a chosen file really is a valid, readable POS database."""
    path = Path(path)
    if not path.exists():
        return False, "That file does not exist."
    if path.stat().st_size == 0:
        return False, "That file is empty."
    try:
        conn = sqlite3.connect(str(path))
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = (row[0] if row else "unknown").lower()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"That file is not a readable database: {exc}"

    if result != "ok":
        return False, f"The backup file is damaged ({result})."
    if "users" not in names or "sales" not in names:
        return False, "That file is not an Iya Ashabi Bukka POS backup."
    return True, "Backup file is valid."


def _restore_into_memory(source: Path) -> None:
    """Replace the contents of the in-memory database with a backup file.

    ATTACH keeps the copy inside SQLite, so no rows pass through Python.
    Foreign keys are suspended for the swap and re-checked afterwards, so a
    damaged backup can never leave the live database half-replaced.
    """
    db.session.commit()
    raw = db.session.connection().connection.driver_connection
    raw.commit()
    previous_level = raw.isolation_level
    raw.isolation_level = None  # autocommit so PRAGMA statements take effect

    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("ATTACH DATABASE ? AS restore_source",
                    (str(Path(source).resolve()),))
        tables = [
            row[0]
            for row in raw.execute(
                "SELECT name FROM restore_source.sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            raw.execute(f'DELETE FROM main."{table}"')
            raw.execute(
                f'INSERT INTO main."{table}" SELECT * FROM restore_source."{table}"'
            )
        problems = raw.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise BackupError(
                "The backup did not pass its integrity checks, so nothing was changed."
            )
        raw.execute("DETACH DATABASE restore_source")
    finally:
        raw.isolation_level = previous_level
        raw.execute("PRAGMA foreign_keys=ON")
        raw.commit()


def restore_backup(source: Path, user=None) -> dict:
    """Replace the active database, always after taking a safety backup."""
    source = Path(source)
    valid, message = validate_backup(source)
    if not valid:
        raise BackupError(message)

    # Capture identity NOW: db.session.remove() below detaches the instance.
    user_id = _user_id(user)
    username = "system"
    if user is not None:
        try:
            username = sqlalchemy.inspect(user).loaded["username"] or "system"
        except Exception:  # noqa: BLE001
            username = "system"

    active = _database_path()

    # 1. Safety backup of the current database BEFORE touching anything.
    safety = None
    try:
        safety = create_backup(user=user, kind=KIND_RESTORE_SAFETY)
    except BackupError as exc:
        raise BackupError(
            f"Restore cancelled: a safety backup of the current database could not be taken. {exc}"
        )

    db.session.remove()
    # Windows will not let us rename over a file that is still open, so the
    # connection pool MUST be closed before the swap and rebuilt afterwards.
    db.engine.dispose()
    try:
        # An in-memory database (test suite) has no file to swap. Instead copy
        # every table across from the backup using ATTACH.
        if active is None:
            _restore_into_memory(source)
            entry = BackupLog(
                filename=source.name, path=str(source), kind=KIND_PRE_RESTORE,
                status="SUCCESS", user_id=user_id,
                message=f"Restored from {source.name} (in-memory database).",
            )
            db.session.add(entry)
            db.session.commit()
            return {
                "ok": True,
                "message": "Database restored. Please sign in again.",
                "safety_backup": safety.filename if safety else None,
                "previous": None,
            }

        # 2. Copy the backup into place via a temp file, then swap atomically so
        #    a failure can never leave a half-written database.
        staged = active.with_suffix(".restoring")
        shutil.copy2(source, staged)
        ok, detail = integrity_check(staged)
        if not ok:
            staged.unlink(missing_ok=True)
            raise BackupError("The backup failed verification during restore. Nothing was changed.")

        previous = active.with_suffix(".previous")
        if active.exists():
            shutil.copy2(active, previous)
        staged.replace(active)
        staged.unlink(missing_ok=True)
        # Re-open a fresh connection against the newly restored file.
        db.engine.dispose()

        # Remove stale WAL/SHM sidecars so they cannot corrupt the new file.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(active) + suffix)
            if sidecar.exists():
                sidecar.unlink(missing_ok=True)

        entry = BackupLog(
            filename=source.name, path=str(source), kind=KIND_PRE_RESTORE,
            status="SUCCESS", user_id=user_id,
            message=f"Restored from {source.name}. Previous database saved as {previous.name}.",
        )
        db.session.add(entry)
        db.session.commit()

        log.warning("Database restored from %s by %s", source.name, username)
        return {
            "ok": True,
            "message": "Database restored. Please sign in again.",
            "safety_backup": safety.filename if safety else None,
            "previous": previous.name,
        }
    except BackupError:
        db.session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.exception("Restore failed")
        raise BackupError(
            "The restore could not be completed. Your existing database has been kept."
        ) from exc


def maybe_auto_backup(user=None) -> BackupLog | None:
    """Run once per day if the owner enabled automatic backups."""
    if not Setting.get_bool("system.auto_backup", True):
        return None

    today = utcnow().date().isoformat()
    last = (
        BackupLog.query.filter(BackupLog.kind == KIND_AUTO, BackupLog.status == "SUCCESS")
        .order_by(BackupLog.created_at.desc())
        .first()
    )
    if last is not None and last.created_at and last.created_at.date().isoformat() == today:
        return None
    try:
        return create_backup(user=user, kind=KIND_AUTO)
    except BackupError as exc:
        log.warning("Automatic backup skipped: %s", exc)
        return None


def delete_backup(entry: BackupLog) -> None:
    if entry.path:
        try:
            Path(entry.path).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not delete backup file: %s", exc)
    db.session.delete(entry)
    db.session.commit()


def verify_active_database() -> tuple[bool, str]:
    return integrity_check(_database_path())

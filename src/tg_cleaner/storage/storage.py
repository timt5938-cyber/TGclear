"""SQLite storage manager for Telegram Cleaner.

Manages persistent database operations for cleanup batches, audit snapshots,
whitelist entries, and application settings. Supports both synchronous (sqlite3)
and asynchronous (aiosqlite) access patterns with WAL mode and foreign key integrity.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import aiosqlite

from tg_cleaner.storage.models import SnapshotRecord

DEFAULT_DB_PATH = r"C:\tg\data\tg_cleaner.db"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    total_candidates INTEGER NOT NULL DEFAULT 0,
    departed_count INTEGER NOT NULL DEFAULT 0,
    restored_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    username TEXT,
    is_private INTEGER NOT NULL DEFAULT 0,
    entity_type TEXT NOT NULL,
    unread_count INTEGER NOT NULL DEFAULT 0,
    dormancy_days INTEGER NOT NULL DEFAULT 0,
    user_inactive_days INTEGER NOT NULL DEFAULT 0,
    trigger_reasons TEXT NOT NULL DEFAULT '',
    left_at TEXT NOT NULL,
    restore_status TEXT NOT NULL DEFAULT 'left',
    restored_at TEXT,
    error_message TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshots_batch_id ON snapshots(batch_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_entity_id ON snapshots(entity_id);

CREATE TABLE IF NOT EXISTS whitelist (
    entity_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    username TEXT,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(db_path: str) -> None:
    """Ensure database parent directory exists."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)


class StorageManager:
    """Synchronous SQLite database manager using Python's standard sqlite3."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        _ensure_dir(self.db_path)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self) -> None:
        """Execute DDL statements to ensure all tables and indexes exist."""
        with self._get_connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def create_batch(
        self,
        batch_id: str,
        total_candidates: int,
        notes: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> None:
        """Register a new cleanup batch run."""
        timestamp = created_at or _now_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO batches (id, created_at, total_candidates, departed_count, restored_count, notes)
                VALUES (?, ?, ?, 0, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    total_candidates = excluded.total_candidates,
                    notes = excluded.notes;
                """,
                (batch_id, timestamp, total_candidates, notes),
            )

    def add_snapshot(self, record: SnapshotRecord) -> None:
        """Store an audit snapshot record prior to departure and increment batch departed count."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO snapshots (
                    batch_id, entity_id, title, username, is_private, entity_type,
                    unread_count, dormancy_days, user_inactive_days, trigger_reasons,
                    left_at, restore_status, restored_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.batch_id,
                    record.entity_id,
                    record.title,
                    record.username,
                    1 if record.is_private else 0,
                    record.entity_type,
                    record.unread_count,
                    record.dormancy_days,
                    record.user_inactive_days,
                    record.trigger_reasons,
                    record.left_at,
                    record.restore_status,
                    record.restored_at,
                    record.error_message,
                ),
            )
            # Synchronize departed count in batches table
            cursor.execute(
                """
                UPDATE batches
                SET departed_count = (SELECT COUNT(*) FROM snapshots WHERE batch_id = ?)
                WHERE id = ?;
                """,
                (record.batch_id, record.batch_id),
            )
            record.id = cursor.lastrowid

    def get_batches(self) -> List[Dict[str, Any]]:
        """Retrieve all cleanup batches ordered newest first."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, created_at, total_candidates, departed_count, restored_count, notes FROM batches ORDER BY created_at DESC;"
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_batch_snapshots(self, batch_id: str) -> List[Dict[str, Any]]:
        """Retrieve all audit snapshot records for a given batch."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, batch_id, entity_id, title, username, is_private, entity_type,
                       unread_count, dormancy_days, user_inactive_days, trigger_reasons,
                       left_at, restore_status, restored_at, error_message
                FROM snapshots
                WHERE batch_id = ?
                ORDER BY id ASC;
                """,
                (batch_id,),
            )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["is_private"] = bool(d["is_private"])
                results.append(d)
            return results

    def update_snapshot_restore_status(
        self,
        snapshot_id: int,
        status: str,
        restored_at: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update rollback/restore status of a specific snapshot and sync batch restored count."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE snapshots
                SET restore_status = ?,
                    restored_at = ?,
                    error_message = ?
                WHERE id = ?;
                """,
                (status, restored_at, error, snapshot_id),
            )
            # Update restored count in the corresponding batch
            cursor.execute(
                """
                UPDATE batches
                SET restored_count = (
                    SELECT COUNT(*) FROM snapshots
                    WHERE batch_id = batches.id AND restore_status = 'restored'
                )
                WHERE id = (SELECT batch_id FROM snapshots WHERE id = ?);
                """,
                (snapshot_id,),
            )

    def get_whitelist(self) -> List[Dict[str, Any]]:
        """Retrieve all whitelisted entities."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT entity_id, title, username, added_at FROM whitelist ORDER BY added_at DESC;"
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_whitelist_ids(self) -> Set[int]:
        """Convenience method to retrieve whitelisted entity IDs as a Set[int]."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT entity_id FROM whitelist;")
            return {row["entity_id"] for row in cursor.fetchall()}

    def add_to_whitelist(
        self,
        entity_id: int,
        title: str,
        username: Optional[str] = None,
    ) -> None:
        """Add or update an entity in the whitelist."""
        now = _now_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO whitelist (entity_id, title, username, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    title = excluded.title,
                    username = excluded.username,
                    added_at = excluded.added_at;
                """,
                (entity_id, title, username, now),
            )

    def remove_from_whitelist(self, entity_id: int) -> None:
        """Remove an entity from the whitelist."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM whitelist WHERE entity_id = ?;", (entity_id,))

    def save_settings(self, settings: Dict[str, Any]) -> None:
        """Persist application settings dictionary to database."""
        with self._get_connection() as conn:
            for key, val in settings.items():
                serialized = json.dumps(val)
                conn.execute(
                    """
                    INSERT INTO settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                    """,
                    (str(key), serialized),
                )

    def get_settings(self) -> Dict[str, Any]:
        """Load application settings dictionary from database."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT key, value FROM settings;")
            result = {}
            for row in cursor.fetchall():
                key = row["key"]
                raw_val = row["value"]
                try:
                    result[key] = json.loads(raw_val)
                except Exception:
                    result[key] = raw_val
            return result


class AsyncStorageManager:
    """Asynchronous SQLite database manager using aiosqlite."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        _ensure_dir(self.db_path)

    async def init_db(self) -> None:
        """Initialize database tables asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.commit()

    async def create_batch(
        self,
        batch_id: str,
        total_candidates: int,
        notes: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> None:
        """Register a new cleanup batch run asynchronously."""
        timestamp = created_at or _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO batches (id, created_at, total_candidates, departed_count, restored_count, notes)
                VALUES (?, ?, ?, 0, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    total_candidates = excluded.total_candidates,
                    notes = excluded.notes;
                """,
                (batch_id, timestamp, total_candidates, notes),
            )
            await db.commit()

    async def add_snapshot(self, record: SnapshotRecord) -> None:
        """Store an audit snapshot record asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO snapshots (
                    batch_id, entity_id, title, username, is_private, entity_type,
                    unread_count, dormancy_days, user_inactive_days, trigger_reasons,
                    left_at, restore_status, restored_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.batch_id,
                    record.entity_id,
                    record.title,
                    record.username,
                    1 if record.is_private else 0,
                    record.entity_type,
                    record.unread_count,
                    record.dormancy_days,
                    record.user_inactive_days,
                    record.trigger_reasons,
                    record.left_at,
                    record.restore_status,
                    record.restored_at,
                    record.error_message,
                ),
            )
            record.id = cursor.lastrowid
            await db.execute(
                """
                UPDATE batches
                SET departed_count = (SELECT COUNT(*) FROM snapshots WHERE batch_id = ?)
                WHERE id = ?;
                """,
                (record.batch_id, record.batch_id),
            )
            await db.commit()

    async def get_batches(self) -> List[Dict[str, Any]]:
        """Retrieve all cleanup batches asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, created_at, total_candidates, departed_count, restored_count, notes FROM batches ORDER BY created_at DESC;"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_batch_snapshots(self, batch_id: str) -> List[Dict[str, Any]]:
        """Retrieve all snapshots for a given batch asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, batch_id, entity_id, title, username, is_private, entity_type,
                       unread_count, dormancy_days, user_inactive_days, trigger_reasons,
                       left_at, restore_status, restored_at, error_message
                FROM snapshots
                WHERE batch_id = ?
                ORDER BY id ASC;
                """,
                (batch_id,),
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["is_private"] = bool(d["is_private"])
                results.append(d)
            return results

    async def update_snapshot_restore_status(
        self,
        snapshot_id: int,
        status: str,
        restored_at: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update snapshot status and batch count asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE snapshots
                SET restore_status = ?,
                    restored_at = ?,
                    error_message = ?
                WHERE id = ?;
                """,
                (status, restored_at, error, snapshot_id),
            )
            await db.execute(
                """
                UPDATE batches
                SET restored_count = (
                    SELECT COUNT(*) FROM snapshots
                    WHERE batch_id = batches.id AND restore_status = 'restored'
                )
                WHERE id = (SELECT batch_id FROM snapshots WHERE id = ?);
                """,
                (snapshot_id,),
            )
            await db.commit()

    async def get_whitelist(self) -> List[Dict[str, Any]]:
        """Retrieve whitelist entries asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT entity_id, title, username, added_at FROM whitelist ORDER BY added_at DESC;"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_whitelist_ids(self) -> Set[int]:
        """Retrieve whitelisted entity IDs as a Set[int] asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT entity_id FROM whitelist;")
            rows = await cursor.fetchall()
            return {row["entity_id"] for row in rows}

    async def add_to_whitelist(
        self,
        entity_id: int,
        title: str,
        username: Optional[str] = None,
    ) -> None:
        """Add or update whitelist entry asynchronously."""
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO whitelist (entity_id, title, username, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    title = excluded.title,
                    username = excluded.username,
                    added_at = excluded.added_at;
                """,
                (entity_id, title, username, now),
            )
            await db.commit()

    async def remove_from_whitelist(self, entity_id: int) -> None:
        """Remove entity from whitelist asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM whitelist WHERE entity_id = ?;", (entity_id,))
            await db.commit()

    async def save_settings(self, settings: Dict[str, Any]) -> None:
        """Persist settings dictionary asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            for key, val in settings.items():
                serialized = json.dumps(val)
                await db.execute(
                    """
                    INSERT INTO settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                    """,
                    (str(key), serialized),
                )
            await db.commit()

    async def get_settings(self) -> Dict[str, Any]:
        """Load settings dictionary asynchronously."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT key, value FROM settings;")
            rows = await cursor.fetchall()
            result = {}
            for row in rows:
                key = row["key"]
                raw_val = row["value"]
                try:
                    result[key] = json.loads(raw_val)
                except Exception:
                    result[key] = raw_val
            return result


# Module-level default singleton for direct function imports
_default_storage = StorageManager()


def create_batch(
    batch_id: str,
    total_candidates: int,
    notes: Optional[str] = None,
    created_at: Optional[str] = None,
) -> None:
    """Create a new cleanup batch record."""
    _default_storage.create_batch(batch_id, total_candidates, notes, created_at)


def add_snapshot(record: SnapshotRecord) -> None:
    """Store an audit snapshot record."""
    _default_storage.add_snapshot(record)


def get_batches() -> List[Dict[str, Any]]:
    """Retrieve all cleanup batches."""
    return _default_storage.get_batches()


def get_batch_snapshots(batch_id: str) -> List[Dict[str, Any]]:
    """Retrieve all snapshots for a given batch ID."""
    return _default_storage.get_batch_snapshots(batch_id)


def update_snapshot_restore_status(
    snapshot_id: int,
    status: str,
    restored_at: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Update restore status and optional error message for a snapshot."""
    _default_storage.update_snapshot_restore_status(snapshot_id, status, restored_at, error)


def get_whitelist() -> List[Dict[str, Any]]:
    """Retrieve all whitelisted entities."""
    return _default_storage.get_whitelist()


def get_whitelist_ids() -> Set[int]:
    """Retrieve all whitelisted entity IDs as a set."""
    return _default_storage.get_whitelist_ids()


def add_to_whitelist(
    entity_id: int,
    title: str,
    username: Optional[str] = None,
) -> None:
    """Add or update an entity in the whitelist."""
    _default_storage.add_to_whitelist(entity_id, title, username)


def remove_from_whitelist(entity_id: int) -> None:
    """Remove an entity from the whitelist."""
    _default_storage.remove_from_whitelist(entity_id)


def save_settings(settings: Dict[str, Any]) -> None:
    """Save application settings."""
    _default_storage.save_settings(settings)


def get_settings() -> Dict[str, Any]:
    """Retrieve application settings."""
    return _default_storage.get_settings()

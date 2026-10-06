"""Comprehensive unit test suite for Telegram Cleaner core-storage components.

Tests models, filter evaluation engine, SQLite storage manager,
and backup export/list functions.
"""

import asyncio
import csv
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

import sys
from pathlib import Path

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tg_cleaner.storage.models import (
    ChatEntity,
    CleanupBatch,
    EntityType,
    FilterConfig,
    RestoreStatus,
    SnapshotRecord,
)
from tg_cleaner.core.filters import evaluate_candidates, evaluate_entity
from tg_cleaner.storage.storage import StorageManager, AsyncStorageManager
from tg_cleaner.storage.backup import export_batch_backup, list_backups


class TestModels(unittest.TestCase):
    """Test domain models definitions and default values."""

    def test_entity_type_and_restore_status_enums(self):
        self.assertEqual(EntityType.CHANNEL, "channel")
        self.assertEqual(EntityType.SUPERGROUP, "supergroup")
        self.assertEqual(EntityType.GROUP, "group")

        self.assertEqual(RestoreStatus.PENDING, "pending")
        self.assertEqual(RestoreStatus.LEFT, "left")
        self.assertEqual(RestoreStatus.RESTORED, "restored")
        self.assertEqual(RestoreStatus.UNRESTORABLE_PRIVATE, "unrestorable_private")
        self.assertEqual(RestoreStatus.FAILED, "failed")

    def test_chat_entity_defaults(self):
        entity = ChatEntity(
            id=12345,
            title="Tech News",
            entity_type="channel",
            is_private=False,
        )
        self.assertEqual(entity.id, 12345)
        self.assertEqual(entity.title, "Tech News")
        self.assertIsNone(entity.username)
        self.assertFalse(entity.is_private)
        self.assertFalse(entity.is_protected)
        self.assertFalse(entity.is_pinned)
        self.assertFalse(entity.is_creator)
        self.assertFalse(entity.is_admin)
        self.assertEqual(entity.unread_count, 0)
        self.assertEqual(entity.dormancy_days, 0)
        self.assertEqual(entity.user_inactive_days, 0)
        self.assertEqual(entity.trigger_reasons, [])
        self.assertFalse(entity.is_candidate)
        self.assertFalse(entity.selected)

    def test_filter_config_defaults(self):
        config = FilterConfig()
        self.assertEqual(config.user_inactive_days, 60)
        self.assertEqual(config.unread_threshold, 200)
        self.assertEqual(config.dormancy_days, 90)
        self.assertEqual(config.logic_mode, "ANY")
        self.assertTrue(config.protect_pinned)
        self.assertTrue(config.protect_admin)
        self.assertTrue(config.filter_user_inactive_enabled)
        self.assertTrue(config.filter_unread_enabled)
        self.assertTrue(config.filter_dormancy_enabled)

    def test_snapshot_record(self):
        rec = SnapshotRecord(
            batch_id="batch_001",
            entity_id=987,
            title="Archived Chat",
            is_private=True,
            entity_type="group",
            left_at="2026-09-25T10:00:00Z",
            restore_status=RestoreStatus.LEFT,
        )
        self.assertEqual(rec.batch_id, "batch_001")
        self.assertEqual(rec.entity_id, 987)
        self.assertTrue(rec.is_private)
        self.assertIsNone(rec.restored_at)

    def test_cleanup_batch(self):
        batch = CleanupBatch(
            id="batch_1",
            created_at="2026-09-25T10:00:00Z",
            total_candidates=10,
        )
        self.assertEqual(batch.total_candidates, 10)
        self.assertEqual(batch.departed_count, 0)
        self.assertEqual(batch.restored_count, 0)


class TestFilters(unittest.TestCase):
    """Test protection logic and candidate evaluation rules."""

    def setUp(self):
        self.config = FilterConfig(
            user_inactive_days=60,
            unread_threshold=200,
            dormancy_days=90,
            logic_mode="ANY",
            protect_pinned=True,
            protect_admin=True,
            filter_user_inactive_enabled=True,
            filter_unread_enabled=True,
            filter_dormancy_enabled=True,
        )

    def test_whitelist_protection(self):
        entity = ChatEntity(
            id=101,
            title="VIP Channel",
            entity_type="channel",
            is_private=False,
            user_inactive_days=100,  # exceeds threshold
        )
        whitelist = {101}
        evaluate_candidates([entity], self.config, whitelist)

        self.assertTrue(entity.is_protected)
        self.assertFalse(entity.is_candidate)
        self.assertFalse(entity.selected)
        self.assertEqual(entity.trigger_reasons, [])

    def test_admin_and_creator_protection(self):
        # Admin protected
        admin_chat = ChatEntity(
            id=102,
            title="My Admin Group",
            entity_type="supergroup",
            is_private=False,
            is_admin=True,
            unread_count=500,
        )
        evaluate_candidates([admin_chat], self.config, set())
        self.assertTrue(admin_chat.is_protected)
        self.assertFalse(admin_chat.is_candidate)

        # Creator protected
        creator_chat = ChatEntity(
            id=103,
            title="My Owned Channel",
            entity_type="channel",
            is_private=True,
            is_creator=True,
            dormancy_days=300,
        )
        evaluate_candidates([creator_chat], self.config, set())
        self.assertTrue(creator_chat.is_protected)
        self.assertFalse(creator_chat.is_candidate)

        # Admin protection disabled
        config_no_admin_protect = self.config.model_copy(update={"protect_admin": False})
        evaluate_candidates([admin_chat], config_no_admin_protect, set())
        self.assertFalse(admin_chat.is_protected)
        self.assertTrue(admin_chat.is_candidate)
        self.assertTrue(admin_chat.selected)

    def test_pinned_protection(self):
        pinned_chat = ChatEntity(
            id=104,
            title="Pinned Chat",
            entity_type="group",
            is_private=False,
            is_pinned=True,
            unread_count=999,
        )
        evaluate_candidates([pinned_chat], self.config, set())
        self.assertTrue(pinned_chat.is_protected)
        self.assertFalse(pinned_chat.is_candidate)

        # Pinned protection disabled
        config_no_pin = self.config.model_copy(update={"protect_pinned": False})
        evaluate_candidates([pinned_chat], config_no_pin, set())
        self.assertFalse(pinned_chat.is_protected)
        self.assertTrue(pinned_chat.is_candidate)

    def test_candidate_any_mode(self):
        # 1 matching trigger out of 3
        e1 = ChatEntity(
            id=201,
            title="Inactive User Only",
            entity_type="channel",
            is_private=False,
            user_inactive_days=65,
            unread_count=10,
            dormancy_days=5,
        )
        evaluate_candidates([e1], self.config, set())
        self.assertTrue(e1.is_candidate)
        self.assertTrue(e1.selected)
        self.assertEqual(len(e1.trigger_reasons), 1)

        # No triggers matching
        e2 = ChatEntity(
            id=202,
            title="Active Chat",
            entity_type="channel",
            is_private=False,
            user_inactive_days=10,
            unread_count=20,
            dormancy_days=2,
        )
        evaluate_candidates([e2], self.config, set())
        self.assertFalse(e2.is_candidate)
        self.assertFalse(e2.selected)
        self.assertEqual(e2.trigger_reasons, [])

    def test_candidate_all_mode(self):
        all_config = self.config.model_copy(update={"logic_mode": "ALL"})

        # Only 2 out of 3 match
        e1 = ChatEntity(
            id=301,
            title="Partially matching",
            entity_type="channel",
            is_private=False,
            user_inactive_days=70,
            unread_count=300,
            dormancy_days=10,  # below 90
        )
        evaluate_candidates([e1], all_config, set())
        self.assertFalse(e1.is_candidate)
        self.assertFalse(e1.selected)
        self.assertEqual(e1.trigger_reasons, [])

        # All 3 match
        e2 = ChatEntity(
            id=302,
            title="Fully matching",
            entity_type="channel",
            is_private=False,
            user_inactive_days=70,
            unread_count=300,
            dormancy_days=100,
        )
        evaluate_candidates([e2], all_config, set())
        self.assertTrue(e2.is_candidate)
        self.assertTrue(e2.selected)
        self.assertEqual(len(e2.trigger_reasons), 3)

    def test_disabled_filters(self):
        config_disabled = self.config.model_copy(
            update={
                "filter_user_inactive_enabled": False,
                "filter_unread_enabled": False,
                "filter_dormancy_enabled": False,
            }
        )
        entity = ChatEntity(
            id=401,
            title="Extreme Inactivity",
            entity_type="channel",
            is_private=False,
            user_inactive_days=999,
            unread_count=9999,
            dormancy_days=999,
        )
        evaluate_candidates([entity], config_disabled, set())
        self.assertFalse(entity.is_candidate)
        self.assertFalse(entity.selected)
        self.assertEqual(entity.trigger_reasons, [])

    def test_recent_read_protection(self):
        # Channel matches dormancy and unread thresholds, BUT user read it 2 days ago
        entity = ChatEntity(
            id=501,
            title="Recently Read Channel",
            entity_type="channel",
            is_private=False,
            user_inactive_days=2,
            unread_count=500,
            dormancy_days=100,
        )
        # With protect_recent_read=True and protect_recent_read_days=2, it must NOT be a candidate
        protect_config = self.config.model_copy(
            update={"protect_recent_read": True, "protect_recent_read_days": 2}
        )
        evaluate_candidates([entity], protect_config, set())
        self.assertTrue(entity.is_protected)
        self.assertFalse(entity.is_candidate)
        self.assertFalse(entity.selected)

        # But if user inactive days exceeds threshold (e.g. 5 days), it CAN be candidate
        entity_older = ChatEntity(
            id=502,
            title="Older Inactive Channel",
            entity_type="channel",
            is_private=False,
            user_inactive_days=5,
            unread_count=500,
            dormancy_days=100,
        )
        evaluate_candidates([entity_older], protect_config, set())
        self.assertFalse(entity_older.is_protected)
        self.assertTrue(entity_older.is_candidate)

    def test_ignore_archived_protection(self):
        archived_entity = ChatEntity(
            id=601,
            title="Archived Chat",
            entity_type="channel",
            is_private=False,
            is_archived=True,
            unread_count=1000,
            dormancy_days=200,
        )
        config_archive = self.config.model_copy(update={"ignore_archived": True})
        evaluate_candidates([archived_entity], config_archive, set())
        self.assertTrue(archived_entity.is_protected)
        self.assertFalse(archived_entity.is_candidate)


class TestStorageManager(unittest.TestCase):
    """Test SQLite persistence operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_cleaner.db")
        self.storage = StorageManager(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_batch_and_snapshots_flow(self):
        # 1. Create batch
        self.storage.create_batch("batch_test_1", 3, notes="Initial test batch")
        batches = self.storage.get_batches()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["id"], "batch_test_1")
        self.assertEqual(batches[0]["total_candidates"], 3)
        self.assertEqual(batches[0]["departed_count"], 0)

        # 2. Add snapshots
        snap1 = SnapshotRecord(
            batch_id="batch_test_1",
            entity_id=1001,
            title="Channel 1",
            username="chan1",
            is_private=False,
            entity_type="channel",
            unread_count=250,
            dormancy_days=100,
            user_inactive_days=70,
            trigger_reasons="Unread; Dormant",
            left_at="2026-09-25T11:00:00Z",
            restore_status="left",
        )
        snap2 = SnapshotRecord(
            batch_id="batch_test_1",
            entity_id=1002,
            title="Private Group 2",
            is_private=True,
            entity_type="group",
            left_at="2026-09-25T11:01:00Z",
            restore_status="left",
        )
        self.storage.add_snapshot(snap1)
        self.storage.add_snapshot(snap2)

        # Check snapshots retrieval
        snaps = self.storage.get_batch_snapshots("batch_test_1")
        self.assertEqual(len(snaps), 2)
        self.assertFalse(snaps[0]["is_private"])
        self.assertTrue(snaps[1]["is_private"])

        # Check departed count synchronized
        batches = self.storage.get_batches()
        self.assertEqual(batches[0]["departed_count"], 2)

        # 3. Update snapshot restore status
        snap1_id = snaps[0]["id"]
        self.storage.update_snapshot_restore_status(
            snapshot_id=snap1_id,
            status="restored",
            restored_at="2026-09-25T11:05:00Z",
            error=None,
        )

        snaps_after = self.storage.get_batch_snapshots("batch_test_1")
        self.assertEqual(snaps_after[0]["restore_status"], "restored")
        self.assertEqual(snaps_after[0]["restored_at"], "2026-09-25T11:05:00Z")

        # Check restored count in batch updated
        batches_after = self.storage.get_batches()
        self.assertEqual(batches_after[0]["restored_count"], 1)

    def test_whitelist_operations(self):
        self.storage.add_to_whitelist(555, "Dev Channel", "dev_chan")
        self.storage.add_to_whitelist(777, "Private Chat")

        whitelist = self.storage.get_whitelist()
        self.assertEqual(len(whitelist), 2)

        ids = self.storage.get_whitelist_ids()
        self.assertEqual(ids, {555, 777})

        self.storage.remove_from_whitelist(555)
        self.assertEqual(self.storage.get_whitelist_ids(), {777})

    def test_settings_operations(self):
        settings_payload = {
            "user_inactive_days": 45,
            "logic_mode": "ALL",
            "protect_pinned": True,
            "custom_metadata": {"theme": "cyberpunk", "sound": False},
        }
        self.storage.save_settings(settings_payload)

        loaded = self.storage.get_settings()
        self.assertEqual(loaded["user_inactive_days"], 45)
        self.assertEqual(loaded["logic_mode"], "ALL")
        self.assertTrue(loaded["protect_pinned"])
        self.assertEqual(loaded["custom_metadata"]["theme"], "cyberpunk")
        self.assertFalse(loaded["custom_metadata"]["sound"])


class TestAsyncStorageManager(unittest.IsolatedAsyncioTestCase):
    """Test asynchronous SQLite operations."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_async_cleaner.db")
        self.storage = AsyncStorageManager(self.db_path)
        await self.storage.init_db()

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_async_workflow(self):
        await self.storage.create_batch("async_b1", 5)
        rec = SnapshotRecord(
            batch_id="async_b1",
            entity_id=99,
            title="Async Entity",
            is_private=False,
            entity_type="channel",
            left_at="now",
        )
        await self.storage.add_snapshot(rec)

        batches = await self.storage.get_batches()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["departed_count"], 1)

        await self.storage.add_to_whitelist(88, "Async WL", "async_wl")
        wl_ids = await self.storage.get_whitelist_ids()
        self.assertIn(88, wl_ids)


class TestBackup(unittest.TestCase):
    """Test backup exporter and inspection functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_and_list_backups(self):
        snapshots = [
            {
                "id": 1,
                "entity_id": 2001,
                "title": "Crypto Signals",
                "username": "crypto_sig",
                "is_private": False,
                "entity_type": "channel",
                "unread_count": 800,
                "dormancy_days": 150,
                "user_inactive_days": 120,
                "trigger_reasons": ["Unread 800", "Dormant 150d"],
                "left_at": "2026-09-25T11:20:00Z",
                "restore_status": "left",
            },
            {
                "id": 2,
                "entity_id": 2002,
                "title": "Old University Group",
                "username": None,
                "is_private": True,
                "entity_type": "group",
                "unread_count": 0,
                "dormancy_days": 500,
                "user_inactive_days": 400,
                "trigger_reasons": "Dormant 500d",
                "left_at": "2026-09-25T11:20:01Z",
                "restore_status": "left",
            },
        ]

        json_path, csv_path = export_batch_backup("batch_xyz", snapshots, backup_dir=self.temp_dir)
        self.assertTrue(os.path.exists(json_path))
        self.assertTrue(os.path.exists(csv_path))

        # Verify JSON
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["metadata"]["batch_id"], "batch_xyz")
            self.assertEqual(data["metadata"]["total_entities"], 2)
            self.assertEqual(data["metadata"]["public_count"], 1)
            self.assertEqual(data["metadata"]["private_count"], 1)
            self.assertEqual(len(data["snapshots"]), 2)

        # Verify CSV
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = list(csv.reader(f))
            self.assertEqual(
                reader[0],
                ["ID", "Title", "Username", "Type", "Private", "Unread", "DormancyDays", "UserInactiveDays", "TriggerReason", "LeftAt", "Status"]
            )
            self.assertEqual(len(reader), 3)  # header + 2 rows
            self.assertEqual(reader[1][0], "2001")
            self.assertEqual(reader[1][1], "Crypto Signals")
            self.assertEqual(reader[1][2], "crypto_sig")
            self.assertEqual(reader[1][4], "False")
            self.assertEqual(reader[2][0], "2002")
            self.assertEqual(reader[2][4], "True")

        # Verify list_backups
        backups = list_backups(self.temp_dir)
        self.assertEqual(len(backups), 2)
        filenames = [b["filename"] for b in backups]
        self.assertTrue(any(fn.endswith(".json") for fn in filenames))
        self.assertTrue(any(fn.endswith(".csv") for fn in filenames))
        for b in backups:
            self.assertEqual(b["batch_id"], "batch_xyz")
            self.assertGreater(b["size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()

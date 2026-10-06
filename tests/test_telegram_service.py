"""Unit and integration tests for Telegram MTProto service.

Tests authentication workflows, dialog scanning safety boundaries,
rate-limited departure engine with audit backups, and rollback engine.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telethon import errors, functions, types

from tg_cleaner.core.telegram_service import (
    MockDialog,
    MockEntity,
    MockTelethonClient,
    TelegramService,
)
from tg_cleaner.storage.backup import list_backups
from tg_cleaner.storage.models import EntityType, FilterConfig, RestoreStatus
from tg_cleaner.storage.storage import StorageManager


class TestTelegramAuth(unittest.IsolatedAsyncioTestCase):
    """Test Telegram authentication state machine and session handling."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_auth.db")
        self.session_path = os.path.join(self.temp_dir, "test_telegram.session")
        self.backup_dir = os.path.join(self.temp_dir, "backups")

        self.storage = StorageManager(self.db_path)
        self.service = TelegramService(
            session_path=self.session_path,
            storage=self.storage,
            backup_dir=self.backup_dir,
            mock_mode=True,
        )

    async def asyncTearDown(self):
        await self.service.disconnect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_initial_auth_state_unauthorized(self):
        state = await self.service.get_auth_state()
        self.assertFalse(state["authorized"])
        self.assertIsNone(state["phone"])
        self.assertIsNone(state["user"])

    async def test_send_code_and_sign_in_flow(self):
        # 1. Send code
        result = await self.service.send_code(
            phone="+1234567890",
            api_id=123456,
            api_hash="mock_hash_abc",
        )
        self.assertEqual(result["status"], "code_sent")
        self.assertEqual(result["phone"], "+1234567890")
        self.assertIsNotNone(result["phone_code_hash"])

        # Verify settings saved in storage
        settings = self.storage.get_settings()
        self.assertEqual(settings["api_id"], 123456)
        self.assertEqual(settings["api_hash"], "mock_hash_abc")
        self.assertEqual(settings["phone"], "+1234567890")

        # 2. Sign in with standard code
        auth_res = await self.service.sign_in(phone="+1234567890", code="12345")
        self.assertEqual(auth_res["status"], "authorized")
        self.assertIsNotNone(auth_res["user"])
        self.assertEqual(auth_res["user"]["phone"], "+1234567890")

        # Verify auth state reports authorized
        state = await self.service.get_auth_state()
        self.assertTrue(state["authorized"])
        self.assertEqual(state["phone"], "+1234567890")
        self.assertIsNotNone(state["user"])

    async def test_sign_in_2fa_flow(self):
        await self.service.send_code(
            phone="+1999888777",
            api_id=987654,
            api_hash="mock_hash_xyz",
        )

        # Trigger 2FA required code
        res_2fa = await self.service.sign_in(phone="+1999888777", code="2fa")
        self.assertEqual(res_2fa["status"], "2fa_required")

        # Complete with password
        pwd_res = await self.service.sign_in_password(password="super_secure_pwd")
        self.assertEqual(pwd_res["status"], "authorized")
        self.assertIsNotNone(pwd_res["user"])

        state = await self.service.get_auth_state()
        self.assertTrue(state["authorized"])

    async def test_logout_cleans_session_and_state(self):
        # Simulate authorized state with a dummy session file on disk
        await self.service.send_code(phone="+1000", api_id=111, api_hash="hash")
        await self.service.sign_in(phone="+1000", code="12345")

        with open(self.session_path, "w", encoding="utf-8") as f:
            f.write("mock_session_data")
        self.assertTrue(os.path.exists(self.session_path))

        logout_res = await self.service.logout()
        self.assertEqual(logout_res["status"], "logged_out")
        self.assertFalse(os.path.exists(self.session_path))

        state = await self.service.get_auth_state()
        self.assertFalse(state["authorized"])


class TestDialogScanner(unittest.IsolatedAsyncioTestCase):
    """Test dialog scanning, safety boundaries, permissions, and metrics."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_scan.db")
        self.session_path = os.path.join(self.temp_dir, "test_scan.session")
        self.backup_dir = os.path.join(self.temp_dir, "backups")

        self.storage = StorageManager(self.db_path)
        self.service = TelegramService(
            session_path=self.session_path,
            storage=self.storage,
            backup_dir=self.backup_dir,
            mock_mode=True,
        )

    async def asyncTearDown(self):
        await self.service.disconnect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_strict_safety_rule_skips_users_and_bots(self):
        """Strict safety rule: Direct messages with users/bots MUST NOT be scanned."""
        now = datetime.now(timezone.utc)
        custom_dialogs = [
            # Human user direct message
            MockDialog(
                entity=MockEntity(entity_id=1, title="Friend Bob", is_user=True),
                unread_count=10,
                dialog_date=now - timedelta(days=5),
            ),
            # Bot direct message
            MockDialog(
                entity=MockEntity(entity_id=2, title="Spam Bot", is_user=True),
                unread_count=50,
                dialog_date=now - timedelta(days=20),
            ),
            # Public channel (should be scanned)
            MockDialog(
                entity=MockEntity(
                    entity_id=101,
                    title="Public Channel",
                    username="pub_chan",
                    is_channel=True,
                    megagroup=False,
                ),
                unread_count=250,
                dialog_date=now - timedelta(days=100),
            ),
        ]

        mock_client = MockTelethonClient(dialogs=custom_dialogs, is_authorized=True)
        self.service.client = mock_client

        progress_events = []

        def on_progress(event):
            progress_events.append(event)

        entities = await self.service.scan_dialogs(progress_callback=on_progress)

        # Ensure Bob and Spam Bot are completely absent
        found_ids = {e.id for e in entities}
        self.assertNotIn(1, found_ids, "Human direct message must not be included")
        self.assertNotIn(2, found_ids, "Bot direct message must not be included")
        self.assertIn(101, found_ids)
        self.assertEqual(len(entities), 1)

        # Check progress events were emitted
        self.assertTrue(len(progress_events) > 0)
        self.assertEqual(progress_events[-1]["found"], 1)

    async def test_entity_classification_and_permissions(self):
        now = datetime.now(timezone.utc)
        admin_rights = types.ChatAdminRights(
            change_info=True,
            post_messages=True,
            edit_messages=True,
            delete_messages=True,
            ban_users=True,
            invite_users=True,
            pin_messages=True,
            add_admins=True,
            anonymous=False,
            manage_call=False,
            other=False,
        )

        custom_dialogs = [
            # 1. Channel
            MockDialog(
                entity=MockEntity(
                    entity_id=11,
                    title="News Broadcast",
                    username="news_feed",
                    is_channel=True,
                    megagroup=False,
                    creator=False,
                ),
                unread_count=15,
                dialog_date=now - timedelta(days=10),
            ),
            # 2. Supergroup where user is creator
            MockDialog(
                entity=MockEntity(
                    entity_id=12,
                    title="My Startup Group",
                    username="my_startup",
                    is_channel=True,
                    megagroup=True,
                    creator=True,
                ),
                unread_count=0,
                dialog_date=now - timedelta(days=1),
            ),
            # 3. Supergroup where user is admin
            MockDialog(
                entity=MockEntity(
                    entity_id=13,
                    title="Moderated Group",
                    username=None,
                    is_channel=True,
                    megagroup=True,
                    creator=False,
                    admin_rights=admin_rights,
                ),
                unread_count=50,
                dialog_date=now - timedelta(days=3),
            ),
            # 4. Normal small group
            MockDialog(
                entity=MockEntity(
                    entity_id=14,
                    title="Family Chat",
                    username=None,
                    is_channel=False,
                    megagroup=False,
                    creator=False,
                ),
                unread_count=0,
                dialog_date=now - timedelta(days=1),
                pinned=True,
            ),
        ]

        mock_client = MockTelethonClient(dialogs=custom_dialogs, is_authorized=True)
        self.service.client = mock_client

        entities = await self.service.scan_dialogs()
        by_id = {e.id: e for e in entities}

        # Verify Channel
        self.assertEqual(by_id[11].entity_type, EntityType.CHANNEL.value)
        self.assertFalse(by_id[11].is_private)
        self.assertEqual(by_id[11].username, "news_feed")
        self.assertFalse(by_id[11].is_creator)
        self.assertFalse(by_id[11].is_admin)
        self.assertFalse(by_id[11].is_pinned)

        # Verify Creator Supergroup
        self.assertEqual(by_id[12].entity_type, EntityType.SUPERGROUP.value)
        self.assertTrue(by_id[12].is_creator)
        self.assertTrue(by_id[12].is_protected)

        # Verify Admin Supergroup
        self.assertEqual(by_id[13].entity_type, EntityType.SUPERGROUP.value)
        self.assertTrue(by_id[13].is_private)
        self.assertTrue(by_id[13].is_admin)
        self.assertTrue(by_id[13].is_protected)

        # Verify Pinned Normal Group
        self.assertEqual(by_id[14].entity_type, EntityType.GROUP.value)
        self.assertTrue(by_id[14].is_pinned)
        self.assertTrue(by_id[14].is_protected)

    async def test_metrics_calculation(self):
        now = datetime.now(timezone.utc)
        dialogs = [
            # Caught up (unread = 0) -> user_inactive_days must be 0
            MockDialog(
                entity=MockEntity(entity_id=21, title="Caught Up Chat"),
                unread_count=0,
                dialog_date=now - timedelta(days=40),
            ),
            # Unread messages exist -> user_inactive_days >= dormancy_days
            MockDialog(
                entity=MockEntity(entity_id=22, title="Unread Chat"),
                unread_count=100,
                dialog_date=now - timedelta(days=30),
            ),
        ]
        mock_client = MockTelethonClient(dialogs=dialogs, is_authorized=True)
        self.service.client = mock_client

        entities = await self.service.scan_dialogs()
        by_id = {e.id: e for e in entities}

        self.assertEqual(by_id[21].dormancy_days, 40)
        # When unread = 0 and channel is dormant for 40 days, user_inactive_days is at least dormancy_days (40)
        self.assertEqual(by_id[21].user_inactive_days, 40)

        self.assertEqual(by_id[22].dormancy_days, 30)
        self.assertGreater(by_id[22].user_inactive_days, 30)

    async def test_filter_config_integration(self):
        # Whitelist one entity
        self.storage.add_to_whitelist(20001, "Crypto Signals Daily")

        config = FilterConfig(
            user_inactive_days=60,
            unread_threshold=200,
            dormancy_days=90,
            logic_mode="ANY",
        )

        entities = await self.service.scan_dialogs(filter_config=config)
        by_id = {e.id: e for e in entities}

        # 20001 was whitelisted -> protected, not candidate
        self.assertTrue(by_id[20001].is_protected)
        self.assertFalse(by_id[20001].is_candidate)

        # 20002 is private dormant channel -> candidate
        self.assertTrue(by_id[20002].is_candidate)
        self.assertTrue(by_id[20002].selected)
        self.assertGreater(len(by_id[20002].trigger_reasons), 0)


class TestDepartureEngine(unittest.IsolatedAsyncioTestCase):
    """Test departure engine: DB batches, snapshots, backup export, rate limiting, and RPCs."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_leave.db")
        self.session_path = os.path.join(self.temp_dir, "test_leave.session")
        self.backup_dir = os.path.join(self.temp_dir, "backups")

        self.storage = StorageManager(self.db_path)
        self.service = TelegramService(
            session_path=self.session_path,
            storage=self.storage,
            backup_dir=self.backup_dir,
            mock_mode=True,
            rate_limit_delay_range=(0.001, 0.002),
        )

    async def asyncTearDown(self):
        await self.service.disconnect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_leave_entities_flow_and_backup_creation(self):
        # Initial scan to populate cached entities
        await self.service.scan_dialogs()

        batch_id = "batch_dep_001"
        target_ids = [20001, 50001]  # 20001 is Channel, 50001 is Group

        progress_events = []

        def on_progress(event):
            progress_events.append(event)

        result = await self.service.leave_entities(
            entity_ids=target_ids,
            batch_id=batch_id,
            progress_callback=on_progress,
        )

        # 1. Summary verification
        self.assertEqual(result["batch_id"], batch_id)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["departed"], 2)
        self.assertEqual(result["failed"], 0)

        # 2. Backup files verification
        self.assertTrue(os.path.exists(result["backup_json"]))
        self.assertTrue(os.path.exists(result["backup_csv"]))

        backups = list_backups(self.backup_dir)
        self.assertEqual(len(backups), 2)  # 1 JSON + 1 CSV

        # 3. Database batch and snapshots verification
        batches = self.storage.get_batches()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["id"], batch_id)
        self.assertEqual(batches[0]["departed_count"], 2)

        snapshots = self.storage.get_batch_snapshots(batch_id)
        self.assertEqual(len(snapshots), 2)
        for s in snapshots:
            self.assertEqual(s["restore_status"], RestoreStatus.LEFT.value)

        # 4. Telethon client RPC requests verification
        mock_client: MockTelethonClient = self.service.client
        left_reqs = mock_client.left_requests
        self.assertEqual(len(left_reqs), 2)
        # Channel leave
        self.assertTrue(any(isinstance(r, functions.channels.LeaveChannelRequest) for r in left_reqs))
        # Group leave
        self.assertTrue(any(isinstance(r, functions.messages.DeleteChatUserRequest) for r in left_reqs))

        # 5. Progress callbacks verification
        statuses = [ev.get("status") for ev in progress_events if "status" in ev]
        self.assertIn("left", statuses)

    async def test_departure_flood_wait_handling(self):
        """Test that FloodWaitError is caught, logged, reported, and retried."""
        await self.service.scan_dialogs()

        # Configure mock client to raise FloodWaitError once
        mock_client: MockTelethonClient = self.service.client
        mock_client.flood_wait_on_departure = 1

        batch_id = "batch_flood_test"
        events = []

        async def async_cb(ev):
            events.append(ev)

        result = await self.service.leave_entities(
            entity_ids=[20001],
            batch_id=batch_id,
            progress_callback=async_cb,
        )

        self.assertEqual(result["departed"], 1)
        fw_events = [e for e in events if e.get("status") == "flood_wait"]
        self.assertEqual(len(fw_events), 1)
        self.assertEqual(fw_events[0]["wait_seconds"], 1)


class TestRollbackEngine(unittest.IsolatedAsyncioTestCase):
    """Test rollback engine: public channel rejoining, private rejection, and audit updates."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_rollback.db")
        self.session_path = os.path.join(self.temp_dir, "test_rollback.session")
        self.backup_dir = os.path.join(self.temp_dir, "backups")

        self.storage = StorageManager(self.db_path)
        self.service = TelegramService(
            session_path=self.session_path,
            storage=self.storage,
            backup_dir=self.backup_dir,
            mock_mode=True,
            rate_limit_delay_range=(0.001, 0.002),
            rollback_delay_range=(0.001, 0.002),
        )

    async def asyncTearDown(self):
        await self.service.disconnect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_rollback_public_and_private_entities(self):
        # 1. Scan and depart from 1 public channel (20001) and 1 private channel (20002)
        await self.service.scan_dialogs()
        batch_id = "batch_rb_001"
        await self.service.leave_entities(
            entity_ids=[20001, 20002],
            batch_id=batch_id,
        )

        progress_events = []

        def on_progress(ev):
            progress_events.append(ev)

        # 2. Rollback entire batch
        rollback_res = await self.service.rollback_entities(
            batch_id=batch_id,
            progress_callback=on_progress,
        )

        self.assertEqual(rollback_res["total"], 2)
        self.assertEqual(rollback_res["restored"], 1)
        self.assertEqual(rollback_res["unrestorable_private"], 1)
        self.assertEqual(rollback_res["failed"], 0)

        # 3. Check DB snapshot statuses
        snapshots = self.storage.get_batch_snapshots(batch_id)
        by_eid = {s["entity_id"]: s for s in snapshots}

        # 20001 (public with username) -> restored
        self.assertEqual(by_eid[20001]["restore_status"], RestoreStatus.RESTORED.value)
        self.assertIsNotNone(by_eid[20001]["restored_at"])

        # 20002 (private without username) -> unrestorable_private
        self.assertEqual(by_eid[20002]["restore_status"], RestoreStatus.UNRESTORABLE_PRIVATE.value)
        self.assertEqual(
            by_eid[20002]["error_message"],
            "Приватный канал без публичного юзернейма. Автоматический возврат невозможен.",
        )

        # 4. Check join request was sent to Telethon
        mock_client: MockTelethonClient = self.service.client
        joined_reqs = mock_client.joined_requests
        self.assertEqual(len(joined_reqs), 1)
        self.assertIn("crypto_signals_daily", joined_reqs[0].channel)

    async def test_selective_rollback_by_snapshot_id(self):
        await self.service.scan_dialogs()
        batch_id = "batch_selective"
        await self.service.leave_entities(
            entity_ids=[20001, 20002],
            batch_id=batch_id,
        )

        snapshots = self.storage.get_batch_snapshots(batch_id)
        snap_20001 = [s for s in snapshots if s["entity_id"] == 20001][0]

        # Selective rollback of ONLY 20001
        res = await self.service.rollback_entities(
            batch_id=batch_id,
            snapshot_ids=[snap_20001["id"]],
        )

        self.assertEqual(res["total"], 1)
        self.assertEqual(res["restored"], 1)
        self.assertEqual(res["unrestorable_private"], 0)

        # 20002 should still be 'left'
        snapshots_after = self.storage.get_batch_snapshots(batch_id)
        by_eid = {s["entity_id"]: s for s in snapshots_after}
        self.assertEqual(by_eid[20001]["restore_status"], RestoreStatus.RESTORED.value)
        self.assertEqual(by_eid[20002]["restore_status"], RestoreStatus.LEFT.value)

    async def test_rollback_flood_wait_handling(self):
        await self.service.scan_dialogs()
        batch_id = "batch_rb_fw"
        await self.service.leave_entities(entity_ids=[20001], batch_id=batch_id)

        mock_client: MockTelethonClient = self.service.client
        mock_client.flood_wait_on_rollback = 1

        events = []
        result = await self.service.rollback_entities(
            batch_id=batch_id,
            progress_callback=lambda ev: events.append(ev),
        )

        self.assertEqual(result["restored"], 1)
        fw_events = [e for e in events if e.get("status") == "flood_wait"]
        self.assertEqual(len(fw_events), 1)


if __name__ == "__main__":
    unittest.main()

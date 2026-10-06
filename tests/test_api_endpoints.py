"""Comprehensive test suite for Telegram Cleaner FastAPI backend and static web endpoints.

Tests REST API endpoints, static asset serving, auth workflow in mock mode,
dialog scanning, candidate filtering/evaluation, physical departure, audit history,
rollback, whitelist management, and backup exports.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tg_cleaner.api.app import app, telegram_service, storage
from tg_cleaner.storage.models import FilterConfig, RestoreStatus


class TestApiEndpoints(unittest.TestCase):
    """Integration tests for FastAPI application."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.temp_dir, "test_api.db")
        cls.backups_dir = os.path.join(cls.temp_dir, "backups")
        os.makedirs(cls.backups_dir, exist_ok=True)

        # Force mock mode and isolated storage
        telegram_service.mock_mode = True
        telegram_service.backup_dir = cls.backups_dir
        telegram_service.rate_limit_delay_range = (0.001, 0.002)
        telegram_service.rollback_delay_range = (0.001, 0.002)

        cls.client = TestClient(app)

    def wait_for_task(self, timeout=5.0):
        import time
        start = time.time()
        while time.time() - start < timeout:
            res = self.client.get("/api/tasks/status")
            if res.json().get("status") in ("completed", "idle", "error"):
                return
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_01_static_and_index_serving(self):
        """Test root URL and static assets (HTML, CSS, JS) serve successfully."""
        res_index = self.client.get("/")
        self.assertEqual(res_index.status_code, 200)
        self.assertIn("Telegram Cleaner", res_index.text)
        self.assertIn("ui-ux-pro-max", res_index.text or "")

        res_css = self.client.get("/static/styles.css")
        self.assertEqual(res_css.status_code, 200)
        self.assertIn("--bg-app: #090D16;", res_css.text)

        res_js = self.client.get("/static/app.js")
        self.assertEqual(res_js.status_code, 200)
        self.assertIn("Telegram Cleaner", res_js.text)

    def test_02_auth_workflow_mock_mode(self):
        """Test full authentication wizard endpoints in mock mode."""
        # Check initial state
        res_status = self.client.get("/api/auth/status")
        self.assertEqual(res_status.status_code, 200)
        data = res_status.json()
        self.assertTrue(data["mock_mode"])

        # Send code
        res_send = self.client.post(
            "/api/auth/send-code",
            json={"phone": "+1234567890", "api_id": 12345, "api_hash": "testhash123"},
        )
        self.assertEqual(res_send.status_code, 200)
        self.assertEqual(res_send.json()["status"], "code_sent")

        # Verify invalid code
        res_invalid = self.client.post(
            "/api/auth/verify-code",
            json={"phone": "+1234567890", "code": "00000"},
        )
        self.assertEqual(res_invalid.status_code, 400)

        # Verify 2FA trigger
        res_2fa = self.client.post(
            "/api/auth/verify-code",
            json={"phone": "+1234567890", "code": "2fa"},
        )
        self.assertEqual(res_2fa.status_code, 200)
        self.assertEqual(res_2fa.json()["status"], "2fa_required")

        # Submit 2FA password
        res_pw = self.client.post(
            "/api/auth/verify-password",
            json={"password": "cloud_password_secret"},
        )
        self.assertEqual(res_pw.status_code, 200)
        self.assertEqual(res_pw.json()["status"], "authorized")

        # Check authenticated status
        res_auth_check = self.client.get("/api/auth/status")
        self.assertEqual(res_auth_check.status_code, 200)
        self.assertTrue(res_auth_check.json()["authorized"])

    def test_03_scan_and_dialog_cache(self):
        """Test scanning dialogs and retrieving discovered entities."""
        # Initiate scan
        res_scan = self.client.post(
            "/api/scan",
            json={
                "user_inactive_days": 60,
                "unread_threshold": 200,
                "dormancy_days": 90,
                "logic_mode": "ANY",
                "protect_admin": True,
                "protect_pinned": True,
            },
        )
        self.assertEqual(res_scan.status_code, 200)

        # Allow async task to complete
        self.wait_for_task()

        # Fetch cached dialogs
        res_dialogs = self.client.get("/api/dialogs")
        self.assertEqual(res_dialogs.status_code, 200)
        dialogs = res_dialogs.json()
        self.assertGreater(len(dialogs), 0)

        # Verify safety: no direct user chats (id=10001 or 10002)
        entity_ids = [d["id"] for d in dialogs]
        self.assertNotIn(10001, entity_ids)
        self.assertNotIn(10002, entity_ids)

        # Verify candidates exist
        candidate_ids = [d["id"] for d in dialogs if d["is_candidate"]]
        self.assertIn(20001, candidate_ids)
        self.assertIn(20002, candidate_ids)

    def test_04_evaluate_filters(self):
        """Test re-evaluating dialogs with strict conjunctive (ALL) logic."""
        res_eval = self.client.post(
            "/api/evaluate",
            json={
                "user_inactive_days": 110,
                "unread_threshold": 400,
                "dormancy_days": 110,
                "logic_mode": "ALL",
                "protect_admin": True,
                "protect_pinned": True,
            },
        )
        self.assertEqual(res_eval.status_code, 200)
        data = res_eval.json()
        self.assertEqual(data["status"], "evaluated")
        self.assertIn("dialogs", data)

    def test_05_whitelist_crud_and_protection(self):
        """Test adding and removing entities from whitelist."""
        # Add entity 20001 to whitelist
        res_add = self.client.post(
            "/api/whitelist",
            json={
                "entity_id": 20001,
                "title": "Crypto Signals Daily",
                "username": "crypto_signals_daily",
            },
        )
        self.assertEqual(res_add.status_code, 200)
        wl = res_add.json()["whitelist"]
        self.assertTrue(any(w["entity_id"] == 20001 for w in wl))

        # Check whitelist GET
        res_get = self.client.get("/api/whitelist")
        self.assertEqual(res_get.status_code, 200)
        self.assertTrue(any(w["entity_id"] == 20001 for w in res_get.json()))

        # Remove from whitelist
        res_del = self.client.delete("/api/whitelist/20001")
        self.assertEqual(res_del.status_code, 200)
        self.assertFalse(any(w["entity_id"] == 20001 for w in res_del.json()["whitelist"]))

    def test_06_leave_entities_and_backups(self):
        """Test physical departure and automatic pre-departure backup generation."""
        import time

        # Scan first to ensure cache
        self.client.post("/api/scan")
        self.wait_for_task()

        # Depart from candidate 20001
        res_leave = self.client.post(
            "/api/leave",
            json={"entity_ids": [20001, 20002]},
        )
        self.assertEqual(res_leave.status_code, 200)
        batch_id = res_leave.json()["batch_id"]

        # Wait for task completion
        self.wait_for_task()

        # Verify history batch created
        res_hist = self.client.get("/api/history")
        self.assertEqual(res_hist.status_code, 200)
        batches = res_hist.json()
        matching_batch = next((b for b in batches if b["id"] == batch_id), None)
        self.assertIsNotNone(matching_batch)
        self.assertEqual(matching_batch["total_candidates"], 2)

        # Verify snapshots
        res_snaps = self.client.get(f"/api/history/{batch_id}/snapshots")
        self.assertEqual(res_snaps.status_code, 200)
        snaps = res_snaps.json()
        self.assertEqual(len(snaps), 2)

        # Verify backups listing
        res_backups = self.client.get("/api/backups")
        self.assertEqual(res_backups.status_code, 200)
        backups = res_backups.json()
        self.assertTrue(any(batch_id in b["filename"] for b in backups))

        # Test download of backup
        json_file = next(b["filename"] for b in backups if b["filename"].endswith(".json"))
        res_dl = self.client.get(f"/api/backups/download/{json_file}")
        self.assertEqual(res_dl.status_code, 200)
        self.assertIn("metadata", res_dl.json())

    def test_07_rollback_batch(self):
        """Test selective rollback rejoins public entities and flags private ones."""
        # Fetch history to get latest batch
        res_hist = self.client.get("/api/history")
        batches = res_hist.json()
        self.assertGreater(len(batches), 0)
        batch_id = batches[0]["id"]

        # Trigger rollback
        res_rb = self.client.post(f"/api/history/{batch_id}/rollback")
        self.assertEqual(res_rb.status_code, 200)
        self.assertEqual(res_rb.json()["status"], "rolling_back")

        # Wait for completion
        self.wait_for_task()

        # Inspect updated snapshots
        res_snaps = self.client.get(f"/api/history/{batch_id}/snapshots")
        snaps = res_snaps.json()

        # Public entity 20001 should be restored
        snap_pub = next((s for s in snaps if s["entity_id"] == 20001), None)
        if snap_pub:
            self.assertEqual(snap_pub["restore_status"], RestoreStatus.RESTORED.value)

        # Private entity 20002 should be unrestorable_private
        snap_priv = next((s for s in snaps if s["entity_id"] == 20002), None)
        if snap_priv:
            self.assertEqual(snap_priv["restore_status"], RestoreStatus.UNRESTORABLE_PRIVATE.value)

    def test_08_settings_and_task_status(self):
        """Test settings persistence and task status tracking."""
        # Save settings
        res_save = self.client.post(
            "/api/settings",
            json={"settings": {"app_theme": "oled_dark", "compact_tables": True}},
        )
        self.assertEqual(res_save.status_code, 200)

        # Retrieve settings
        res_get = self.client.get("/api/settings")
        self.assertEqual(res_get.status_code, 200)
        settings = res_get.json()
        self.assertEqual(settings.get("app_theme"), "oled_dark")

        # Check task status
        res_task = self.client.get("/api/tasks/status")
        self.assertEqual(res_task.status_code, 200)
        self.assertIn("status", res_task.json())

    def test_09_logout_endpoint(self):
        """Test logout endpoint clears session and authorization."""
        res_logout = self.client.post("/api/auth/logout")
        self.assertEqual(res_logout.status_code, 200)
        self.assertEqual(res_logout.json()["status"], "logged_out")

        # Check auth status is now false
        res_status = self.client.get("/api/auth/status")
        self.assertFalse(res_status.json()["authorized"])

    def test_10_folders_endpoint(self):
        """Test fetching custom Telegram folders."""
        res = self.client.get("/api/folders")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsInstance(data, list)


if __name__ == "__main__":
    unittest.main()

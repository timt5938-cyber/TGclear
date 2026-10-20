import base64
import os
from pathlib import Path

import pytest

from tg_cleaner.storage.backup import export_batch_backup, list_backups
from tg_cleaner.storage.crypto import DataCipher
from tg_cleaner.storage.storage import EncryptedStorageManager, StorageManager
from tg_cleaner.core.telegram_service import TelegramService
from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession


KEY = base64.b64encode(bytes(range(32))).decode()


def test_remote_database_is_encrypted_and_migrates(tmp_path):
    path = tmp_path / "tg_cleaner.db"
    legacy = StorageManager(str(path))
    legacy.save_settings({"api_hash": "private-test-value"})
    cipher = DataCipher(KEY, "tenant-a")
    secure = EncryptedStorageManager(str(path), cipher)
    assert secure.get_settings()["api_hash"] == "private-test-value"
    assert not path.exists()
    sealed = (tmp_path / "tg_cleaner.db.enc").read_bytes()
    assert sealed.startswith(b"TGC1")
    assert b"private-test-value" not in sealed
    assert EncryptedStorageManager(str(path), cipher).get_settings()["api_hash"] == "private-test-value"
    with pytest.raises(Exception):
        EncryptedStorageManager(str(path), DataCipher(KEY, "tenant-b"))


def test_backup_is_encrypted_but_downloadable(tmp_path):
    cipher = DataCipher(KEY, "tenant-a")
    json_path, csv_path = export_batch_backup("batch", [{"title": "Secret channel"}], str(tmp_path), cipher)
    assert list_backups(str(tmp_path))[0]["file_type"] in {"json", "csv"}
    for path in (json_path, csv_path):
        blob = Path(path).read_bytes()
        assert b"Secret channel" not in blob
        assert b"Secret channel" in cipher.decrypt(blob, "backup:" + Path(path).name.removesuffix(".enc"))
        with pytest.raises(Exception):
            DataCipher(KEY, "tenant-b").decrypt(blob, "backup:" + Path(path).name.removesuffix(".enc"))


def test_telegram_session_migrates_without_plaintext(tmp_path):
    path = tmp_path / "telegram.session"
    old = SQLiteSession(str(path))
    old.set_dc(2, "149.154.167.40", 443)
    old.auth_key = AuthKey(os.urandom(256))
    original_key = old.auth_key.key
    old.save()
    old.close()
    cipher = DataCipher(KEY, "tenant-a")
    service = TelegramService(session_path=str(path), storage=StorageManager(str(tmp_path / "test.db")), cipher=cipher, mock_mode=True)
    loaded = service._load_session()
    assert loaded.auth_key.key == original_key
    assert not path.exists()
    assert (tmp_path / "telegram.session.enc").read_bytes().startswith(b"TGC1")
    assert service._load_session().auth_key.key == original_key

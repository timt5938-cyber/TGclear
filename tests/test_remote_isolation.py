"""Remote access must never share Telegram state between VELLIUM accounts."""

import time

from fastapi.testclient import TestClient

from tg_cleaner.api import app as api_module


def test_remote_api_requires_login_and_isolates_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(api_module, "REMOTE_MODE", True)
    monkeypatch.setattr(api_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(api_module, "BACKUPS_DIR", tmp_path / "backups")
    api_module._tenants.clear()

    async def verify(token: str) -> str:
        return {
            "a": "11111111-1111-4111-8111-111111111111",
            "b": "22222222-2222-4222-8222-222222222222",
        }[token]

    monkeypatch.setattr(api_module, "verify_user", verify)
    client = TestClient(api_module.app)
    assert client.get("/api/whitelist").status_code == 401
    a = {"Authorization": "Bearer a"}
    b = {"Authorization": "Bearer b"}
    assert client.post("/api/whitelist", headers=a, json={"entity_id": 42, "title": "A"}).status_code == 200
    assert len(client.get("/api/whitelist", headers=a).json()) == 1
    assert client.get("/api/whitelist", headers=b).json() == []
    assert client.post("/api/leave", headers=a, json={"entity_ids": [42]}).status_code == 400
    assert client.post("/api/backups/open-folder", headers=a).status_code == 404
    assert client.post("/api/settings", headers=a, json={"settings": {"mock_mode": True}}).status_code == 400
    api_module.tenant_for(verify_user_id("a")).telegram_service.mock_mode = True
    assert client.post("/api/scan", headers=a, json={}).status_code == 200
    for _ in range(30):
        if client.get("/api/tasks/status", headers=a).json()["status"] == "completed":
            break
        time.sleep(0.05)
    assert client.get("/api/tasks/status", headers=a).json()["status"] == "completed"
    assert client.get("/api/tasks/status", headers=b).json()["status"] == "idle"
    assert client.get("/api/dialogs", headers=b).json() == []


def verify_user_id(token: str) -> str:
    return "11111111-1111-4111-8111-111111111111" if token == "a" else "22222222-2222-4222-8222-222222222222"

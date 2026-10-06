"""FastAPI backend server for Telegram Cleaner.

Provides REST and WebSocket endpoints for MTProto authentication, dialog scanning,
activity filter evaluation, physical entity departure, audit history & selective rollback,
whitelist management, backup inspection/export, and application settings.
"""

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tg_cleaner.core.filters import evaluate_candidates, evaluate_entity
from tg_cleaner.core.telegram_service import TelegramService
from tg_cleaner.storage.backup import list_backups
from tg_cleaner.storage.models import ChatEntity, FilterConfig, SnapshotRecord
from tg_cleaner.storage.storage import StorageManager

logger = logging.getLogger("tg_cleaner.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Paths configuration
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DATA_DIR = BASE_DIR / "data"
BACKUPS_DIR = BASE_DIR / "backups"

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUPS_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# WebSocket Connection Manager
# -----------------------------------------------------------------------------

class ConnectionManager:
    """Manages active WebSocket connections grouped by event channel."""

    def __init__(self) -> None:
        self.active_channels: Dict[str, List[WebSocket]] = {
            "scan": [],
            "leave": [],
            "rollback": [],
            "events": [],
        }

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        """Accept and register client connection."""
        await websocket.accept()
        if channel not in self.active_channels:
            self.active_channels[channel] = []
        self.active_channels[channel].append(websocket)
        logger.info("WebSocket connected to channel '%s' (total: %d)", channel, len(self.active_channels[channel]))

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        """Unregister client connection."""
        if channel in self.active_channels and websocket in self.active_channels[channel]:
            self.active_channels[channel].remove(websocket)
            logger.info("WebSocket disconnected from channel '%s'", channel)

    async def broadcast(self, channel: str, message: Dict[str, Any]) -> None:
        """Broadcast JSON message to subscribers on the given channel and 'events' channel."""
        targets: Set[WebSocket] = set()
        if channel in self.active_channels:
            targets.update(self.active_channels[channel])
        if "events" in self.active_channels:
            targets.update(self.active_channels["events"])

        dead_connections: List[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                dead_connections.append(ws)

        for dead in dead_connections:
            for ch in self.active_channels.values():
                if dead in ch:
                    ch.remove(dead)


ws_manager = ConnectionManager()

# -----------------------------------------------------------------------------
# Application State
# -----------------------------------------------------------------------------

storage = StorageManager(db_path=str(DATA_DIR / "tg_cleaner.db"))

# Check mock mode setting or environment variable
is_mock_mode = (
    os.environ.get("TG_MOCK_MODE", "").strip().lower() in ("1", "true", "yes")
    or bool(storage.get_settings().get("mock_mode", False))
)

telegram_service = TelegramService(
    session_path=str(DATA_DIR / "telegram.session"),
    storage=storage,
    backup_dir=str(BACKUPS_DIR),
    mock_mode=is_mock_mode,
)

# In-memory cache for current dialog scan results
cached_dialogs: List[ChatEntity] = []

# Background task state tracker
class TaskTracker:
    def __init__(self) -> None:
        self.task_type: Optional[str] = None
        self.status: str = "idle"  # idle, running, completed, error, cancelled
        self.progress: Dict[str, Any] = {}
        self.async_task: Optional[asyncio.Task] = None
        self.cancel_requested: bool = False

    def start(self, task_type: str) -> None:
        self.task_type = task_type
        self.status = "running"
        self.progress = {"status": "started", "timestamp": datetime.now(timezone.utc).isoformat()}
        self.cancel_requested = False

    def update(self, data: Dict[str, Any]) -> None:
        self.progress.update(data)

    def finish(self, status: str = "completed", result: Optional[Dict[str, Any]] = None) -> None:
        self.status = status
        if result:
            self.progress.update(result)
        self.progress["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.async_task = None


task_tracker = TaskTracker()

# -----------------------------------------------------------------------------
# FastAPI App Initialization
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Telegram Cleaner API",
    description="High-performance backend for Telegram dialog scanning, filtering, departure and rollback.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Request & Response Schemas
# -----------------------------------------------------------------------------

class SendCodeRequest(BaseModel):
    phone: str
    api_id: int
    api_hash: str


class VerifyCodeRequest(BaseModel):
    phone: str
    code: str


class VerifyPasswordRequest(BaseModel):
    password: str


class LeaveRequest(BaseModel):
    entity_ids: List[int]


class RollbackRequest(BaseModel):
    snapshot_ids: Optional[List[int]] = None


class WhitelistAddRequest(BaseModel):
    entity_id: int
    title: str
    username: Optional[str] = None


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]


# -----------------------------------------------------------------------------
# Authentication Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/auth/status")
async def get_auth_status() -> Dict[str, Any]:
    """Retrieve current Telegram client authorization status."""
    try:
        state = await telegram_service.get_auth_state()
        state["mock_mode"] = telegram_service.mock_mode
        return state
    except Exception as exc:
        logger.error("Error checking auth status: %s", exc)
        return {
            "authorized": False,
            "phone": None,
            "user": None,
            "mock_mode": telegram_service.mock_mode,
            "error": str(exc),
        }


@app.post("/api/auth/send-code")
async def send_code(req: SendCodeRequest) -> Dict[str, Any]:
    """Send SMS/Telegram verification code to user phone and save credentials."""
    try:
        res = await telegram_service.send_code(
            phone=req.phone.strip(),
            api_id=req.api_id,
            api_hash=req.api_hash.strip(),
        )
        return res
    except Exception as exc:
        logger.error("Failed to send code: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Failed to send code",
        )


@app.post("/api/auth/verify-code")
async def verify_code(req: VerifyCodeRequest) -> Dict[str, Any]:
    """Submit one-time code to complete Telegram sign-in."""
    try:
        res = await telegram_service.sign_in(
            phone=req.phone.strip(),
            code=req.code.strip(),
        )
        return res
    except Exception as exc:
        logger.error("Failed to verify code: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Invalid code",
        )


@app.post("/api/auth/verify-password")
async def verify_password(req: VerifyPasswordRequest) -> Dict[str, Any]:
    """Submit 2FA cloud password to complete Telegram sign-in."""
    try:
        res = await telegram_service.sign_in_password(password=req.password)
        return res
    except Exception as exc:
        logger.error("Failed to verify 2FA password: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Invalid 2FA password",
        )


@app.post("/api/auth/logout")
async def logout() -> Dict[str, Any]:
    """Log out of Telegram and purge MTProto session."""
    global cached_dialogs
    try:
        res = await telegram_service.logout()
        cached_dialogs.clear()
        return res
    except Exception as exc:
        logger.error("Error during logout: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


# -----------------------------------------------------------------------------
# Dialogs & Scan Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/folders")
async def get_telegram_folders() -> List[Dict[str, Any]]:
    """Fetch custom Telegram folders for folder-based protection."""
    try:
        folders = await telegram_service.get_dialog_filters()
        return folders
    except Exception as exc:
        logger.error("Error fetching Telegram folders: %s", exc)
        return []


@app.get("/api/dialogs")
async def get_dialogs() -> List[Dict[str, Any]]:
    """Return currently cached discovered dialog entities."""
    return [d.model_dump() for d in cached_dialogs]


async def _run_scan_task(config: Optional[FilterConfig]) -> None:
    """Background runner for scanning dialogs."""
    global cached_dialogs
    task_tracker.start("scan")
    await ws_manager.broadcast("scan", {"type": "start", "action": "scan"})

    async def _progress_cb(data: Dict[str, Any]) -> None:
        if task_tracker.cancel_requested:
            raise asyncio.CancelledError("Scan cancelled by user")
        task_tracker.update(data)
        await ws_manager.broadcast("scan", {"type": "progress", "action": "scan", "data": data})

    try:
        dialogs = await telegram_service.scan_dialogs(
            progress_callback=_progress_cb,
            filter_config=config,
        )
        cached_dialogs = dialogs
        task_tracker.finish("completed", {"total_found": len(dialogs)})
        await ws_manager.broadcast(
            "scan",
            {
                "type": "done",
                "action": "scan",
                "total": len(dialogs),
                "dialogs": [d.model_dump() for d in dialogs],
            },
        )
    except asyncio.CancelledError:
        logger.info("Scan task was cancelled")
        task_tracker.finish("cancelled")
        await ws_manager.broadcast("scan", {"type": "cancelled", "action": "scan"})
    except Exception as exc:
        logger.error("Error during scan task: %s", exc)
        task_tracker.finish("error", {"error": str(exc)})
        await ws_manager.broadcast("scan", {"type": "error", "action": "scan", "error": str(exc)})


@app.post("/api/scan")
async def start_scan(
    config: Optional[FilterConfig] = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> Dict[str, Any]:
    """Initiate dialog discovery scan in background task."""
    if task_tracker.status == "running":
        return {
            "status": "already_running",
            "task_type": task_tracker.task_type,
            "message": "Another task is currently running",
        }

    # Save filter settings if provided
    if config:
        storage.save_settings({"filter_config": config.model_dump()})

    task_tracker.start("scan")
    background_tasks.add_task(_run_scan_task, config)

    return {"status": "scanning", "message": "Dialog scan initiated"}


@app.post("/api/evaluate")
async def evaluate_current_dialogs(config: FilterConfig) -> Dict[str, Any]:
    """Re-evaluate existing cached dialogs against updated FilterConfig and Whitelist."""
    global cached_dialogs
    whitelist_ids = storage.get_whitelist_ids()
    evaluated = evaluate_candidates(cached_dialogs, config, whitelist_ids)
    cached_dialogs = evaluated

    # Save updated filter preferences
    storage.save_settings({"filter_config": config.model_dump()})

    candidates_count = sum(1 for d in evaluated if d.is_candidate)
    protected_count = sum(1 for d in evaluated if d.is_protected)

    return {
        "status": "evaluated",
        "total": len(evaluated),
        "candidates_count": candidates_count,
        "protected_count": protected_count,
        "dialogs": [d.model_dump() for d in evaluated],
    }


# -----------------------------------------------------------------------------
# Departure (Leave) Endpoints
# -----------------------------------------------------------------------------

async def _run_leave_task(entity_ids: List[int], batch_id: str) -> None:
    """Background runner for physical departure from candidate entities."""
    global cached_dialogs
    task_tracker.start("leave")
    await ws_manager.broadcast(
        "leave",
        {"type": "start", "action": "leave", "batch_id": batch_id, "total": len(entity_ids)},
    )

    async def _progress_cb(data: Dict[str, Any]) -> None:
        if task_tracker.cancel_requested:
            raise asyncio.CancelledError("Leave operation cancelled by user")
        task_tracker.update(data)
        await ws_manager.broadcast("leave", {"type": "progress", "action": "leave", "data": data})

    try:
        result = await telegram_service.leave_entities(
            entity_ids=entity_ids,
            batch_id=batch_id,
            progress_callback=_progress_cb,
        )

        # Remove departed entities from memory cache
        departed_set = set(entity_ids)
        cached_dialogs = [d for d in cached_dialogs if d.id not in departed_set]

        task_tracker.finish("completed", result)
        await ws_manager.broadcast(
            "leave",
            {"type": "done", "action": "leave", "result": result},
        )
    except asyncio.CancelledError:
        logger.info("Leave task was cancelled")
        task_tracker.finish("cancelled")
        await ws_manager.broadcast("leave", {"type": "cancelled", "action": "leave"})
    except Exception as exc:
        logger.error("Error during leave task: %s", exc)
        task_tracker.finish("error", {"error": str(exc)})
        await ws_manager.broadcast("leave", {"type": "error", "action": "leave", "error": str(exc)})


@app.post("/api/leave")
async def leave_entities(
    req: LeaveRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> Dict[str, Any]:
    """Start physical departure from specified candidate entities."""
    if not req.entity_ids:
        raise HTTPException(status_code=400, detail="No entity IDs provided")

    if task_tracker.status == "running":
        return {
            "status": "already_running",
            "task_type": task_tracker.task_type,
            "message": "Another task is currently running",
        }

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    task_tracker.start("leave")
    background_tasks.add_task(_run_leave_task, req.entity_ids, batch_id)

    return {
        "status": "leaving",
        "batch_id": batch_id,
        "total": len(req.entity_ids),
        "message": f"Leave operation started for {len(req.entity_ids)} entities",
    }


# -----------------------------------------------------------------------------
# History & Rollback Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/history")
async def get_history() -> List[Dict[str, Any]]:
    """Retrieve all cleanup batches with their corresponding snapshot records."""
    batches = storage.get_batches()
    for b in batches:
        snapshots = storage.get_batch_snapshots(b["id"])
        b["snapshots"] = snapshots
    return batches


@app.get("/api/history/{batch_id}/snapshots")
async def get_batch_snapshots(batch_id: str) -> List[Dict[str, Any]]:
    """Retrieve snapshot records for a specific cleanup batch."""
    return storage.get_batch_snapshots(batch_id)


async def _run_rollback_task(batch_id: str, snapshot_ids: Optional[List[int]]) -> None:
    """Background runner for rejoining left entities."""
    task_tracker.start("rollback")
    await ws_manager.broadcast(
        "rollback",
        {"type": "start", "action": "rollback", "batch_id": batch_id},
    )

    async def _progress_cb(data: Dict[str, Any]) -> None:
        if task_tracker.cancel_requested:
            raise asyncio.CancelledError("Rollback cancelled by user")
        task_tracker.update(data)
        await ws_manager.broadcast("rollback", {"type": "progress", "action": "rollback", "data": data})

    try:
        result = await telegram_service.rollback_entities(
            batch_id=batch_id,
            snapshot_ids=snapshot_ids,
            progress_callback=_progress_cb,
        )
        task_tracker.finish("completed", result)
        await ws_manager.broadcast(
            "rollback",
            {"type": "done", "action": "rollback", "result": result},
        )
    except asyncio.CancelledError:
        logger.info("Rollback task was cancelled")
        task_tracker.finish("cancelled")
        await ws_manager.broadcast("rollback", {"type": "cancelled", "action": "rollback"})
    except Exception as exc:
        logger.error("Error during rollback task: %s", exc)
        task_tracker.finish("error", {"error": str(exc)})
        await ws_manager.broadcast("rollback", {"type": "error", "action": "rollback", "error": str(exc)})


@app.post("/api/history/{batch_id}/rollback")
async def rollback_batch(
    batch_id: str,
    req: Optional[RollbackRequest] = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> Dict[str, Any]:
    """Execute selective or complete rollback for a cleanup batch."""
    if task_tracker.status == "running":
        return {
            "status": "already_running",
            "task_type": task_tracker.task_type,
            "message": "Another task is currently running",
        }

    snapshot_ids = req.snapshot_ids if req else None
    task_tracker.start("rollback")
    background_tasks.add_task(_run_rollback_task, batch_id, snapshot_ids)

    return {
        "status": "rolling_back",
        "batch_id": batch_id,
        "message": "Rollback operation initiated",
    }


# -----------------------------------------------------------------------------
# Whitelist Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/whitelist")
async def get_whitelist() -> List[Dict[str, Any]]:
    """Retrieve all whitelisted entities."""
    return storage.get_whitelist()


@app.post("/api/whitelist")
async def add_to_whitelist(req: WhitelistAddRequest) -> Dict[str, Any]:
    """Add entity to whitelist and re-evaluate cached dialogs."""
    global cached_dialogs
    storage.add_to_whitelist(
        entity_id=req.entity_id,
        title=req.title,
        username=req.username,
    )

    # Re-evaluate cached dialogs so UI reflects protection immediately
    if cached_dialogs:
        settings = storage.get_settings()
        filter_cfg_dict = settings.get("filter_config")
        cfg = FilterConfig(**filter_cfg_dict) if filter_cfg_dict else FilterConfig()
        whitelist_ids = storage.get_whitelist_ids()
        cached_dialogs = evaluate_candidates(cached_dialogs, cfg, whitelist_ids)

    return {"status": "ok", "whitelist": storage.get_whitelist()}


@app.delete("/api/whitelist/{entity_id}")
async def remove_from_whitelist(entity_id: int) -> Dict[str, Any]:
    """Remove entity from whitelist and re-evaluate cached dialogs."""
    global cached_dialogs
    storage.remove_from_whitelist(entity_id)

    # Re-evaluate cached dialogs
    if cached_dialogs:
        settings = storage.get_settings()
        filter_cfg_dict = settings.get("filter_config")
        cfg = FilterConfig(**filter_cfg_dict) if filter_cfg_dict else FilterConfig()
        whitelist_ids = storage.get_whitelist_ids()
        cached_dialogs = evaluate_candidates(cached_dialogs, cfg, whitelist_ids)

    return {"status": "ok", "whitelist": storage.get_whitelist()}


# -----------------------------------------------------------------------------
# Backups Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/backups")
async def get_backups() -> List[Dict[str, Any]]:
    """List all available JSON and CSV backup dumps."""
    return list_backups(telegram_service.backup_dir)


@app.post("/api/backups/open-folder")
async def open_backups_folder() -> Dict[str, Any]:
    """Open backup directory in Windows File Explorer."""
    backup_path = os.path.abspath(telegram_service.backup_dir)
    os.makedirs(backup_path, exist_ok=True)

    try:
        if sys.platform == "win32":
            os.startfile(backup_path)
        else:
            subprocess.Popen(["xdg-open", backup_path])
        return {"status": "ok", "path": backup_path}
    except Exception as exc:
        logger.warning("os.startfile failed, trying explorer subprocess: %s", exc)
        try:
            subprocess.Popen(["explorer", backup_path])
            return {"status": "ok", "path": backup_path}
        except Exception as e2:
            logger.error("Failed to open explorer: %s", e2)
            raise HTTPException(status_code=500, detail=str(e2))


@app.get("/api/backups/download/{filename}")
async def download_backup_file(filename: str):
    """Serve backup JSON or CSV file for download."""
    clean_name = os.path.basename(filename)
    target = Path(telegram_service.backup_dir) / clean_name

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found")

    media_type = "application/json" if clean_name.endswith(".json") else "text/csv; charset=utf-8"
    return FileResponse(
        path=str(target),
        filename=clean_name,
        media_type=media_type,
    )


# -----------------------------------------------------------------------------
# Settings & Task Management Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/settings")
async def get_settings() -> Dict[str, Any]:
    """Retrieve application settings."""
    settings = storage.get_settings()
    settings["mock_mode"] = telegram_service.mock_mode
    return settings


@app.post("/api/settings")
async def save_settings(req: SettingsUpdateRequest) -> Dict[str, Any]:
    """Save application settings."""
    storage.save_settings(req.settings)

    # Allow dynamic toggling of mock_mode if present in settings
    if "mock_mode" in req.settings:
        telegram_service.mock_mode = bool(req.settings["mock_mode"])

    return {"status": "ok", "settings": storage.get_settings()}


@app.get("/api/tasks/status")
async def get_task_status() -> Dict[str, Any]:
    """Return status and latest progress of background tasks."""
    return {
        "task_type": task_tracker.task_type,
        "status": task_tracker.status,
        "progress": task_tracker.progress,
    }


@app.post("/api/tasks/cancel")
async def cancel_task() -> Dict[str, Any]:
    """Request cancellation of current running background task."""
    if task_tracker.status == "running":
        task_tracker.cancel_requested = True
        if task_tracker.async_task and not task_tracker.async_task.done():
            task_tracker.async_task.cancel()
        return {"status": "cancelling", "message": "Cancellation requested"}
    return {"status": "idle", "message": "No task currently running"}


# -----------------------------------------------------------------------------
# WebSocket Endpoints
# -----------------------------------------------------------------------------

@app.websocket("/ws/scan")
async def ws_scan_endpoint(websocket: WebSocket) -> None:
    """Stream live scan progress updates."""
    await ws_manager.connect(websocket, "scan")
    try:
        while True:
            # Keepalive / ping
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "scan")
    except Exception:
        ws_manager.disconnect(websocket, "scan")


@app.websocket("/ws/leave")
async def ws_leave_endpoint(websocket: WebSocket) -> None:
    """Stream live leave departure progress updates."""
    await ws_manager.connect(websocket, "leave")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "leave")
    except Exception:
        ws_manager.disconnect(websocket, "leave")


@app.websocket("/ws/rollback")
async def ws_rollback_endpoint(websocket: WebSocket) -> None:
    """Stream live rollback rejoin progress updates."""
    await ws_manager.connect(websocket, "rollback")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "rollback")
    except Exception:
        ws_manager.disconnect(websocket, "rollback")


@app.websocket("/ws/events")
async def ws_events_endpoint(websocket: WebSocket) -> None:
    """Stream all application events across channels."""
    await ws_manager.connect(websocket, "events")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "events")
    except Exception:
        ws_manager.disconnect(websocket, "events")


# -----------------------------------------------------------------------------
# Static Files & UI Mounting
# -----------------------------------------------------------------------------

@app.get("/")
async def serve_index() -> FileResponse:
    """Serve the single-page application entrypoint."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "Frontend index.html not found. Ensure static assets are generated."},
        )
    return FileResponse(index_file)


# Mount static directory to serve styles.css, app.js, etc.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

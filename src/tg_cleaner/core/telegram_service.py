"""Telegram service implementation using Telethon MTProto client.

Provides session management, authentication workflows, safe dialog scanning,
rate-limited physical departure from candidate entities, and selective rollback.
Includes offline mock support for robust CI/test environments without live credentials.
"""

import asyncio
import inspect
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from telethon import TelegramClient, errors, functions, types

from tg_cleaner.storage.backup import export_batch_backup
from tg_cleaner.storage.models import (
    ChatEntity,
    EntityType,
    FilterConfig,
    RestoreStatus,
    SnapshotRecord,
)
from tg_cleaner.storage.storage import StorageManager

logger = logging.getLogger("tg_cleaner.telegram_service")

DEFAULT_SESSION_PATH = r"C:\tg\data\telegram.session"
DEFAULT_BACKUP_DIR = r"C:\tg\backups"


async def _emit_callback(
    cb: Optional[Callable[[Dict[str, Any]], Any]],
    data: Dict[str, Any],
) -> None:
    """Emit progress data to sync or async callback function safely."""
    if not cb:
        return
    try:
        if inspect.iscoroutinefunction(cb):
            await cb(data)
        else:
            cb(data)
    except Exception as exc:
        logger.warning("Error in progress_callback: %s", exc)


class MockEntity:
    """Mock Telegram entity representing a channel, supergroup, group, or user."""

    def __init__(
        self,
        entity_id: int,
        title: str,
        username: Optional[str] = None,
        is_channel: bool = True,
        megagroup: bool = False,
        creator: bool = False,
        admin_rights: Optional[Any] = None,
        is_user: bool = False,
        phone: Optional[str] = None,
    ) -> None:
        self.id = entity_id
        self.title = title
        self.username = username
        self.megagroup = megagroup
        self.creator = creator
        self.admin_rights = admin_rights
        self._is_user = is_user
        self._is_channel = is_channel
        self.phone = phone
        self.first_name = title
        self.last_name = None


class MockDialog:
    """Mock dialog object matching Telethon custom Dialog interface."""

    def __init__(
        self,
        entity: MockEntity,
        unread_count: int = 0,
        dialog_date: Optional[datetime] = None,
        pinned: bool = False,
        user_inactive_days: Optional[int] = None,
        read_inbox_max_id: int = 0,
        archived: bool = False,
        folder_id: Optional[int] = None,
    ) -> None:
        self.entity = entity
        self.id = entity.id
        self.title = entity.title
        self.name = entity.title
        self.unread_count = unread_count
        self.pinned = pinned
        self.date = dialog_date
        self.user_inactive_days = user_inactive_days
        self.archived = archived
        self.folder_id = folder_id
        self.is_user = entity._is_user
        self.is_channel = entity._is_channel and not entity._is_user
        self.is_group = (not entity._is_user) and (entity.megagroup or not entity._is_channel)

        class _InnerDialog:
            def __init__(self, p: bool, r_max: int, unread: int) -> None:
                self.pinned = p
                self.read_inbox_max_id = r_max
                self.unread_count = unread

        self.dialog = _InnerDialog(pinned, read_inbox_max_id, unread_count)


class MockSentCode:
    """Mock response for send_code_request."""

    def __init__(self, phone_code_hash: str = "mock_phone_code_hash_98765") -> None:
        self.phone_code_hash = phone_code_hash


class MockTelethonClient:
    """Mock Telethon client for offline testing and CI environments."""

    def __init__(
        self,
        session: Any = None,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        is_authorized: bool = False,
        user_info: Optional[Dict[str, Any]] = None,
        dialogs: Optional[List[Any]] = None,
        flood_wait_on_departure: int = 0,
        flood_wait_on_rollback: int = 0,
    ) -> None:
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self._connected = False
        self._authorized = is_authorized
        self._user_info = user_info or {
            "id": 7770001,
            "first_name": "Test",
            "last_name": "Account",
            "username": "test_account",
            "phone": "+1234567890",
        }
        self.dialogs: List[Any] = dialogs if dialogs is not None else self._create_default_dialogs()
        self.flood_wait_on_departure = flood_wait_on_departure
        self.flood_wait_on_rollback = flood_wait_on_rollback
        self.left_requests: List[Any] = []
        self.joined_requests: List[Any] = []

    def _create_default_dialogs(self) -> List[MockDialog]:
        """Create realistic mock dialogs covering all entity boundaries and conditions."""
        now = datetime.now(timezone.utc)
        from datetime import timedelta

        return [
            # 1. Direct message with human user (MUST BE SKIPPED)
            MockDialog(
                entity=MockEntity(
                    entity_id=10001,
                    title="Alice Smith",
                    username="alicesmith",
                    is_user=True,
                ),
                unread_count=5,
                dialog_date=now - timedelta(days=1),
                pinned=False,
            ),
            # 2. Direct message with bot (MUST BE SKIPPED)
            MockDialog(
                entity=MockEntity(
                    entity_id=10002,
                    title="Alert Bot",
                    username="alert_bot",
                    is_user=True,
                ),
                unread_count=100,
                dialog_date=now - timedelta(days=50),
                pinned=False,
            ),
            # 3. Public dormant channel with username (Eligible candidate)
            MockDialog(
                entity=MockEntity(
                    entity_id=20001,
                    title="Crypto Signals Daily",
                    username="crypto_signals_daily",
                    is_channel=True,
                    megagroup=False,
                    creator=False,
                    admin_rights=None,
                ),
                unread_count=450,
                dialog_date=now - timedelta(days=120),
                pinned=False,
                user_inactive_days=120,
            ),
            # 4. Private dormant channel without username (Eligible candidate, unrestorable)
            MockDialog(
                entity=MockEntity(
                    entity_id=20002,
                    title="Secret Private Club",
                    username=None,
                    is_channel=True,
                    megagroup=False,
                    creator=False,
                    admin_rights=None,
                ),
                unread_count=300,
                dialog_date=now - timedelta(days=100),
                pinned=False,
                user_inactive_days=100,
            ),
            # 5. Supergroup where user is administrator (Protected entity)
            MockDialog(
                entity=MockEntity(
                    entity_id=30001,
                    title="Python Devs Community",
                    username="python_devs_group",
                    is_channel=True,
                    megagroup=True,
                    creator=False,
                    admin_rights=types.ChatAdminRights(
                        change_info=True,
                        post_messages=True,
                        edit_messages=True,
                        delete_messages=True,
                        ban_users=True,
                        invite_users=True,
                        pin_messages=True,
                        add_admins=False,
                        anonymous=False,
                        manage_call=False,
                        other=False,
                    ),
                ),
                unread_count=800,
                dialog_date=now - timedelta(days=150),
                pinned=False,
                user_inactive_days=150,
            ),
            # 6. Channel pinned by user (Protected entity)
            MockDialog(
                entity=MockEntity(
                    entity_id=40001,
                    title="Pinned Announcement Channel",
                    username="pinned_news",
                    is_channel=True,
                    megagroup=False,
                    creator=False,
                    admin_rights=None,
                ),
                unread_count=900,
                dialog_date=now - timedelta(days=200),
                pinned=True,
                user_inactive_days=200,
            ),
            # 7. Normal small group chat (Active, unread=0 -> inactive=0, not candidate)
            MockDialog(
                entity=MockEntity(
                    entity_id=50001,
                    title="Weekend Hikers",
                    username=None,
                    is_channel=False,
                    megagroup=False,
                    creator=False,
                    admin_rights=None,
                ),
                unread_count=0,
                dialog_date=now - timedelta(days=2),
                pinned=False,
                user_inactive_days=0,
            ),
        ]

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def is_user_authorized(self) -> bool:
        return self._authorized

    async def get_me(self) -> Any:
        class _UserMe:
            def __init__(self, data: Dict[str, Any]) -> None:
                self.id = data["id"]
                self.first_name = data["first_name"]
                self.last_name = data.get("last_name")
                self.username = data.get("username")
                self.phone = data.get("phone")

        return _UserMe(self._user_info)

    async def send_code_request(self, phone: str) -> MockSentCode:
        return MockSentCode(phone_code_hash=f"mock_hash_for_{phone}")

    async def sign_in(
        self,
        phone: Optional[str] = None,
        code: Optional[str] = None,
        password: Optional[str] = None,
        phone_code_hash: Optional[str] = None,
    ) -> Any:
        if password is not None:
            if password == "wrong_password":
                raise errors.PasswordHashInvalidError(request=None)
            self._authorized = True
            return await self.get_me()

        if code is not None:
            if code in ("2fa", "22222"):
                raise errors.SessionPasswordNeededError(request=None)
            if code in ("invalid", "00000"):
                raise errors.PhoneCodeInvalidError(request=None)
            if phone:
                self._user_info["phone"] = phone
            self._authorized = True
            return await self.get_me()

        raise errors.PhoneCodeInvalidError(request=None)

    async def log_out(self) -> bool:
        self._authorized = False
        return True

    async def iter_dialogs(self):
        for d in self.dialogs:
            yield d

    async def get_input_entity(self, entity_id: Any) -> Any:
        return entity_id

    async def get_entity(self, entity_id: Any) -> Any:
        for d in self.dialogs:
            if d.id == entity_id or getattr(d.entity, "username", None) == entity_id:
                return d.entity
        return MockEntity(entity_id=int(entity_id) if str(entity_id).isdigit() else 99999, title=f"Chat {entity_id}")

    async def __call__(self, request: Any) -> Any:
        """Handle Telethon RPC requests."""
        if isinstance(request, functions.channels.LeaveChannelRequest):
            if self.flood_wait_on_departure > 0:
                self.flood_wait_on_departure -= 1
                fw = errors.FloodWaitError(request=None)
                fw.seconds = 1
                raise fw
            self.left_requests.append(request)
            return True

        if isinstance(request, functions.messages.DeleteChatUserRequest):
            if self.flood_wait_on_departure > 0:
                self.flood_wait_on_departure -= 1
                fw = errors.FloodWaitError(request=None)
                fw.seconds = 1
                raise fw
            self.left_requests.append(request)
            return True

        if isinstance(request, functions.channels.JoinChannelRequest):
            if self.flood_wait_on_rollback > 0:
                self.flood_wait_on_rollback -= 1
                fw = errors.FloodWaitError(request=None)
                fw.seconds = 1
                raise fw
            self.joined_requests.append(request)
            return True

        return True


class TelegramService:
    """Async service orchestrating Telegram MTProto client operations."""

    def __init__(
        self,
        session_path: str = DEFAULT_SESSION_PATH,
        storage: Optional[StorageManager] = None,
        backup_dir: str = DEFAULT_BACKUP_DIR,
        mock_mode: bool = False,
        client: Optional[Any] = None,
        rate_limit_delay_range: Optional[Tuple[float, float]] = None,
        rollback_delay_range: Optional[Tuple[float, float]] = None,
    ) -> None:
        self.session_path = session_path
        self.storage = storage or StorageManager()
        self.backup_dir = backup_dir
        self.mock_mode = mock_mode
        self.client: Optional[Any] = client

        # Configurable rate limiting delay ranges
        if rate_limit_delay_range is not None:
            self.rate_limit_delay_range = rate_limit_delay_range
        else:
            self.rate_limit_delay_range = (0.01, 0.02) if mock_mode else (1.5, 2.5)

        if rollback_delay_range is not None:
            self.rollback_delay_range = rollback_delay_range
        else:
            self.rollback_delay_range = (0.01, 0.02) if mock_mode else (2.0, 3.5)

        self._cached_entities: Dict[int, ChatEntity] = {}

    def _ensure_session_dir(self) -> None:
        """Ensure session file directory exists."""
        session_dir = os.path.dirname(os.path.abspath(self.session_path))
        os.makedirs(session_dir, exist_ok=True)

    def _get_or_create_client(
        self,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
    ) -> Any:
        """Get or initialize the underlying Telegram client."""
        if self.mock_mode:
            if self.client is None or not isinstance(self.client, MockTelethonClient):
                self.client = MockTelethonClient(
                    session=self.session_path,
                    api_id=api_id,
                    api_hash=api_hash,
                )
            return self.client

        if self.client is None:
            settings = self.storage.get_settings()
            cfg_api_id = api_id or settings.get("api_id")
            cfg_api_hash = api_hash or settings.get("api_hash")

            if not cfg_api_id or not cfg_api_hash:
                raise ValueError("Telegram API credentials (api_id, api_hash) are not configured.")

            self._ensure_session_dir()
            self.client = TelegramClient(
                self.session_path,
                int(cfg_api_id),
                str(cfg_api_hash),
            )
        return self.client

    async def _ensure_connected(self) -> Any:
        """Ensure client is instantiated and connected."""
        client = self._get_or_create_client()
        if hasattr(client, "is_connected") and not client.is_connected():
            await client.connect()
        return client

    async def connect(self) -> None:
        """Explicitly connect the MTProto client."""
        await self._ensure_connected()

    async def disconnect(self) -> None:
        """Disconnect the MTProto client if connected."""
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning("Error during disconnect: %s", e)

    # -------------------------------------------------------------------------
    # Authentication Methods
    # -------------------------------------------------------------------------

    async def get_auth_state(self) -> Dict[str, Any]:
        """Check current authentication state.

        Returns:
            Dict containing 'authorized' (bool), 'phone' (Optional[str]),
            and 'user' (Optional[dict]).
        """
        settings = self.storage.get_settings()
        stored_phone = settings.get("phone")

        if self.mock_mode:
            client = self._get_or_create_client()
            authorized = await client.is_user_authorized()
            if authorized:
                me = await client.get_me()
                user_dict = {
                    "id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone": me.phone,
                }
                return {"authorized": True, "phone": me.phone or stored_phone, "user": user_dict}
            return {"authorized": False, "phone": stored_phone, "user": None}

        # Check if credentials exist
        api_id = settings.get("api_id")
        api_hash = settings.get("api_hash")
        if not api_id or not api_hash:
            return {"authorized": False, "phone": stored_phone, "user": None}

        try:
            client = await self._ensure_connected()
            authorized = await client.is_user_authorized()
            if authorized:
                me = await client.get_me()
                user_dict = {
                    "id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone": me.phone,
                }
                return {"authorized": True, "phone": me.phone or stored_phone, "user": user_dict}
            return {"authorized": False, "phone": stored_phone, "user": None}
        except Exception as e:
            logger.warning("Error checking auth state: %s", e)
            return {"authorized": False, "phone": stored_phone, "user": None}

    async def send_code(self, phone: str, api_id: int, api_hash: str) -> Dict[str, Any]:
        """Initialize client, send verification code to phone, and store credentials.

        Args:
            phone: Phone number with country code.
            api_id: Telegram API ID.
            api_hash: Telegram API hash string.

        Returns:
            Status dictionary with phone and phone_code_hash.
        """
        # Persist credentials in settings
        self.storage.save_settings({
            "api_id": int(api_id),
            "api_hash": str(api_hash),
            "phone": str(phone).strip(),
        })

        if self.mock_mode:
            client = self._get_or_create_client(api_id=int(api_id), api_hash=str(api_hash))
            await client.connect()
            sent = await client.send_code_request(phone)
            self.storage.save_settings({"phone_code_hash": sent.phone_code_hash})
            return {
                "status": "code_sent",
                "phone": phone,
                "phone_code_hash": sent.phone_code_hash,
            }

        # Live client setup
        if self.client is not None:
            await self.disconnect()
            self.client = None

        self._ensure_session_dir()
        self.client = TelegramClient(self.session_path, int(api_id), str(api_hash))
        await self.client.connect()

        res = await self.client.send_code_request(phone)
        phone_code_hash = getattr(res, "phone_code_hash", None)

        self.storage.save_settings({
            "phone_code_hash": phone_code_hash,
        })

        return {
            "status": "code_sent",
            "phone": phone,
            "phone_code_hash": phone_code_hash,
        }

    async def sign_in(self, phone: str, code: str) -> Dict[str, Any]:
        """Sign in with phone and code received via SMS/Telegram.

        Args:
            phone: Phone number.
            code: One-time verification code.

        Returns:
            Dict with status 'authorized' and user details, or '2fa_required'.
        """
        settings = self.storage.get_settings()
        phone_code_hash = settings.get("phone_code_hash")
        client = await self._ensure_connected()

        try:
            if self.mock_mode:
                user = await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            else:
                user = await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)

            user_dict = {
                "id": getattr(user, "id", None),
                "first_name": getattr(user, "first_name", None),
                "last_name": getattr(user, "last_name", None),
                "username": getattr(user, "username", None),
                "phone": getattr(user, "phone", phone),
            }
            return {"status": "authorized", "user": user_dict}

        except errors.SessionPasswordNeededError:
            return {"status": "2fa_required"}

    async def sign_in_password(self, password: str) -> Dict[str, Any]:
        """Complete 2FA verification using cloud password.

        Args:
            password: The 2FA password.

        Returns:
            Dict with status 'authorized' and user details.
        """
        client = await self._ensure_connected()
        user = await client.sign_in(password=password)
        user_dict = {
            "id": getattr(user, "id", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
            "username": getattr(user, "username", None),
            "phone": getattr(user, "phone", None),
        }
        return {"status": "authorized", "user": user_dict}

    async def logout(self) -> Dict[str, Any]:
        """Log out, remove MTProto session files, and clear session state."""
        if self.client is not None:
            try:
                await self.client.log_out()
            except Exception as e:
                logger.warning("Error during log_out call: %s", e)
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning("Error during disconnect call: %s", e)
            self.client = None

        # Clean session files from disk
        base = self.session_path
        candidates = [base]
        if not base.endswith(".session"):
            candidates.append(base + ".session")

        for p in candidates:
            for suffix in ["", "-journal", "-wal", "-shm"]:
                target = p + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError as e:
                        logger.warning("Failed to delete session file %s: %s", target, e)

        # Clear active phone_code_hash from settings
        self.storage.save_settings({"phone_code_hash": None})
        self._cached_entities.clear()

        return {"status": "logged_out"}

    # -------------------------------------------------------------------------
    # Dialog Filters / Folders
    # -------------------------------------------------------------------------

    async def get_dialog_filters(self) -> List[Dict[str, Any]]:
        """Fetch custom Telegram folders (dialog filters) and their member peer IDs."""
        client = await self._ensure_connected()
        if self.mock_mode:
            return [
                {"id": 3, "title": "❤️‍🔥", "emoticon": "🔥", "peer_ids": [30001], "count": 1},
                {"id": 4, "title": "🤡", "emoticon": "🤡", "peer_ids": [20001], "count": 1},
                {"id": 5, "title": "📰", "emoticon": "📰", "peer_ids": [20002], "count": 1},
            ]
        try:
            from telethon import utils
            res = await client(functions.messages.GetDialogFiltersRequest())
            raw_filters = getattr(res, "filters", res) if not isinstance(res, list) else res
            folders = []
            for f in raw_filters:
                fid = getattr(f, "id", None)
                if fid is None or fid == 0:
                    continue
                title_obj = getattr(f, "title", None)
                title = getattr(title_obj, "text", str(title_obj or f"Папка {fid}"))
                emoticon = getattr(f, "emoticon", None) or ""
                peers = getattr(f, "pinned_peers", []) + getattr(f, "include_peers", [])
                peer_ids = set()
                for p in peers:
                    try:
                        pid = utils.get_peer_id(p)
                        peer_ids.add(pid)
                        cid = getattr(p, "channel_id", getattr(p, "chat_id", None))
                        if cid:
                            peer_ids.add(cid)
                            peer_ids.add(-cid)
                            peer_ids.add(int(f"-100{cid}"))
                    except Exception:
                        pass
                folders.append({
                    "id": fid,
                    "title": title,
                    "emoticon": emoticon,
                    "peer_ids": list(peer_ids),
                    "count": len(peers),
                })
            return folders
        except Exception as e:
            logger.warning("Error fetching dialog filters: %s", e)
            return []

    # -------------------------------------------------------------------------
    # Dialog Scanner
    # -------------------------------------------------------------------------

    async def scan_dialogs(
        self,
        progress_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        filter_config: Optional[FilterConfig] = None,
    ) -> List[ChatEntity]:
        """Scan user dialogs excluding personal direct messages and bots.

        Strict safety rule:
            Any dialog where `is_user` is True is strictly skipped.

        Returns:
            List of discovered channel and group ChatEntity objects.
        """
        client = await self._ensure_connected()
        whitelist_ids: Set[int] = self.storage.get_whitelist_ids()

        # Load Telegram folders to tag dialogs and enforce folder protection
        folders = await self.get_dialog_filters()
        peer_to_folders: Dict[int, List[Dict[str, Any]]] = {}
        for fold in folders:
            fid = fold["id"]
            fname = fold["title"]
            for pid in fold["peer_ids"]:
                peer_to_folders.setdefault(pid, []).append({"id": fid, "title": fname})

        entities: List[ChatEntity] = []
        scanned_count = 0
        now = datetime.now(timezone.utc)

        async for dialog in client.iter_dialogs():
            scanned_count += 1

            # Strict safety rule 1:
            # Check dialog.is_user: if True, SKIP COMPLETELY.
            # Neither human users nor bots are scanned or touched!
            if getattr(dialog, "is_user", False):
                continue
            entity = getattr(dialog, "entity", None)
            if entity is not None and isinstance(entity, types.User):
                continue

            # Strict safety rule 2:
            # Check archive: do not take chats from archive!
            is_archived = bool(getattr(dialog, "archived", False) or getattr(dialog, "folder_id", None) == 1)
            if (filter_config is None or filter_config.ignore_archived) and is_archived:
                continue

            # Process channels and groups:
            # Channel / Supergroup: dialog.is_channel
            # Regular group: dialog.is_group
            is_channel = bool(getattr(dialog, "is_channel", False))
            is_group = bool(getattr(dialog, "is_group", False))
            if not is_channel and not is_group:
                continue

            if entity is None:
                continue

            entity_id = getattr(dialog, "id", getattr(entity, "id", None))
            if entity_id is None:
                continue

            # Determine entity type:
            # 'channel' if is_channel and not getattr(dialog.entity, 'megagroup', False),
            # else 'supergroup' / 'group'.
            is_megagroup = bool(getattr(entity, "megagroup", False))
            if is_channel and not is_megagroup:
                entity_type = EntityType.CHANNEL.value
            elif is_megagroup:
                entity_type = EntityType.SUPERGROUP.value
            else:
                entity_type = EntityType.GROUP.value

            title = getattr(dialog, "title", None) or getattr(dialog, "name", None) or getattr(entity, "title", f"Chat {entity_id}")
            raw_username = getattr(entity, "username", None)
            username = str(raw_username).strip() if raw_username else None
            is_private = (username is None or len(username) == 0)

            # Check permissions on entity:
            # is_creator: check dialog.entity.creator or chat.creator
            is_creator = bool(getattr(entity, "creator", False))
            # is_admin: check dialog.entity.admin_rights is not None or participant rights
            admin_rights = getattr(entity, "admin_rights", None)
            is_admin = admin_rights is not None
            # is_pinned: dialog.pinned is True
            is_pinned = bool(getattr(dialog, "pinned", False))

            # Calculate metrics:
            unread_count = int(getattr(dialog, "unread_count", 0) or 0)

            # dormancy_days: days since dialog.date (last message in chat). If no date, 0.
            dialog_date = getattr(dialog, "date", None)
            if dialog_date:
                if dialog_date.tzinfo is None:
                    dialog_date = dialog_date.replace(tzinfo=timezone.utc)
                else:
                    dialog_date = dialog_date.astimezone(timezone.utc)
                dormancy_days = max(0, int((now - dialog_date).total_seconds() / 86400.0))
            else:
                dormancy_days = 0

            # user_inactive_days:
            # If unread_count == 0, user read up to the latest post, so inactive days is dormancy_days.
            # If unread_count > 0, inactive days is at least dormancy_days plus unread offset.
            explicit_inactive = getattr(dialog, "user_inactive_days", None)
            if explicit_inactive is not None:
                user_inactive_days = int(explicit_inactive)
            elif unread_count == 0:
                user_inactive_days = dormancy_days
            else:
                user_inactive_days = dormancy_days + max(1, int(unread_count / 10))

            # Match folders for entity
            matched_folds = peer_to_folders.get(entity_id, [])
            if not matched_folds and getattr(entity, "id", None):
                matched_folds = peer_to_folders.get(entity.id, [])
            folder_names = [f["title"] for f in matched_folds]
            folder_ids = [f["id"] for f in matched_folds]

            # Check baseline protection (admin/creator, pinned, in whitelist, or in protected folder)
            is_folder_protected = bool(
                filter_config
                and filter_config.protected_folder_ids
                and any(fid in filter_config.protected_folder_ids for fid in folder_ids)
            )
            is_protected = is_creator or is_admin or is_pinned or (entity_id in whitelist_ids) or is_folder_protected

            chat_entity = ChatEntity(
                id=entity_id,
                title=title,
                username=username,
                entity_type=entity_type,
                is_private=is_private,
                is_protected=is_protected,
                is_pinned=is_pinned,
                is_creator=is_creator,
                is_admin=is_admin,
                is_archived=is_archived,
                folders=folder_names,
                folder_ids=folder_ids,
                unread_count=unread_count,
                dormancy_days=dormancy_days,
                user_inactive_days=user_inactive_days,
                trigger_reasons=[],
                is_candidate=False,
                selected=False,
            )

            if filter_config is not None:
                from tg_cleaner.core.filters import evaluate_entity
                evaluate_entity(chat_entity, filter_config, whitelist_ids)

            entities.append(chat_entity)
            self._cached_entities[entity_id] = chat_entity

            await _emit_callback(
                progress_callback,
                {
                    "scanned": scanned_count,
                    "found": len(entities),
                    "current": title,
                },
            )

        return entities

    # -------------------------------------------------------------------------
    # Departure Engine
    # -------------------------------------------------------------------------

    async def leave_entities(
        self,
        entity_ids: List[int],
        batch_id: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        """Safely depart from confirmed candidate entities.

        Creates audit batch and snapshot records in DB and exports persistent
        JSON and CSV backup files BEFORE performing any API departure calls.

        Args:
            entity_ids: List of Telegram chat/channel IDs to leave.
            batch_id: Unique identifier for this cleanup batch.
            progress_callback: Optional callback receiving progress event dicts.

        Returns:
            Dict containing batch_id, total, departed, failed, and backup file paths.
        """
        client = await self._ensure_connected()

        # 1. Resolve entity metadata
        candidates: Dict[int, ChatEntity] = {}
        for eid in entity_ids:
            if eid in self._cached_entities:
                candidates[eid] = self._cached_entities[eid]
            else:
                # Try to fetch from client or build minimal representation
                try:
                    raw_ent = await client.get_entity(eid)
                    raw_user = getattr(raw_ent, "username", None)
                    is_mg = getattr(raw_ent, "megagroup", False)
                    is_chan = getattr(raw_ent, "broadcast", False) or is_mg
                    candidates[eid] = ChatEntity(
                        id=eid,
                        title=getattr(raw_ent, "title", f"Chat {eid}"),
                        username=str(raw_user).strip() if raw_user else None,
                        entity_type="supergroup" if is_mg else ("channel" if is_chan else "group"),
                        is_private=raw_user is None,
                    )
                except Exception:
                    candidates[eid] = ChatEntity(
                        id=eid,
                        title=f"Chat {eid}",
                        entity_type="channel",
                        is_private=False,
                    )

        # 2. BEFORE ANY LEAVE: create batch record in DB
        self.storage.create_batch(
            batch_id=batch_id,
            total_candidates=len(entity_ids),
            notes="Physical departure cleanup batch",
        )

        # 3. Create SnapshotRecord for every entity and write all into DB
        now_iso = datetime.now(timezone.utc).isoformat()
        snapshot_db_ids: Dict[int, int] = {}
        for eid in entity_ids:
            ce = candidates[eid]
            reasons_str = "; ".join(ce.trigger_reasons) if ce.trigger_reasons else ""
            rec = SnapshotRecord(
                batch_id=batch_id,
                entity_id=eid,
                title=ce.title,
                username=ce.username,
                is_private=ce.is_private,
                entity_type=ce.entity_type,
                unread_count=ce.unread_count,
                dormancy_days=ce.dormancy_days,
                user_inactive_days=ce.user_inactive_days,
                trigger_reasons=reasons_str,
                left_at=now_iso,
                restore_status=RestoreStatus.PENDING.value,
            )
            self.storage.add_snapshot(rec)
            if rec.id is not None:
                snapshot_db_ids[eid] = rec.id

        # 4. Immediately export JSON and CSV backup to C:\tg\backups\
        initial_snapshots = self.storage.get_batch_snapshots(batch_id)
        json_path, csv_path = export_batch_backup(
            batch_id=batch_id,
            snapshots=initial_snapshots,
            backup_dir=self.backup_dir,
        )
        logger.info(
            "Batch %s pre-departure backup exported: JSON=%s, CSV=%s",
            batch_id,
            json_path,
            csv_path,
        )

        departed_count = 0
        failed_count = 0

        # 5. Process physical departure with rate limiting & flood wait handling
        for idx, eid in enumerate(entity_ids):
            ce = candidates[eid]
            title = ce.title
            snap_id = snapshot_db_ids.get(eid)

            # Rate-limiting delay: await asyncio.sleep(random.uniform(1.5, 2.5))
            delay = random.uniform(*self.rate_limit_delay_range)
            await asyncio.sleep(delay)

            leave_success = False
            last_error = None
            max_retries = 3

            for attempt in range(max_retries):
                try:
                    try:
                        if ce.entity_type in (EntityType.CHANNEL.value, EntityType.SUPERGROUP.value):
                            channel_input = await client.get_input_entity(eid)
                            await client(functions.channels.LeaveChannelRequest(channel=channel_input))
                        else:
                            try:
                                await client(functions.messages.DeleteChatUserRequest(chat_id=abs(eid), user_id="me"))
                            except Exception:
                                channel_input = await client.get_input_entity(eid)
                                await client(functions.channels.LeaveChannelRequest(channel=channel_input))
                    except Exception:
                        channel_input = await client.get_input_entity(eid)
                        await client(functions.channels.LeaveChannelRequest(channel=channel_input))

                    leave_success = True
                    break

                except errors.FloodWaitError as e:
                    last_error = e
                    logger.warning("FloodWaitError on entity %s: sleeping %d seconds", title, e.seconds)
                    await _emit_callback(
                        progress_callback,
                        {
                            "status": "flood_wait",
                            "wait_seconds": e.seconds,
                            "entity": title,
                        },
                    )
                    await asyncio.sleep(e.seconds + 1)

                except Exception as e:
                    last_error = e
                    logger.error("Error leaving entity %s (%d): %s", title, eid, e)
                    break

            # Update snapshot status in DB
            if leave_success:
                departed_count += 1
                if snap_id is not None:
                    self.storage.update_snapshot_restore_status(
                        snapshot_id=snap_id,
                        status=RestoreStatus.LEFT.value,
                        error=None,
                    )
                await _emit_callback(
                    progress_callback,
                    {
                        "current": idx + 1,
                        "total": len(entity_ids),
                        "entity": title,
                        "status": RestoreStatus.LEFT.value,
                    },
                )
            else:
                failed_count += 1
                if snap_id is not None:
                    self.storage.update_snapshot_restore_status(
                        snapshot_id=snap_id,
                        status=RestoreStatus.FAILED.value,
                        error=str(last_error),
                    )
                await _emit_callback(
                    progress_callback,
                    {
                        "current": idx + 1,
                        "total": len(entity_ids),
                        "entity": title,
                        "status": RestoreStatus.FAILED.value,
                        "error": str(last_error),
                    },
                )

        # 6. Re-export backup JSON and CSV reflecting final departed statuses
        final_snapshots = self.storage.get_batch_snapshots(batch_id)
        json_path, csv_path = export_batch_backup(
            batch_id=batch_id,
            snapshots=final_snapshots,
            backup_dir=self.backup_dir,
        )

        return {
            "batch_id": batch_id,
            "total": len(entity_ids),
            "departed": departed_count,
            "failed": failed_count,
            "backup_json": json_path,
            "backup_csv": csv_path,
        }

    # -------------------------------------------------------------------------
    # Rollback Engine
    # -------------------------------------------------------------------------

    async def rollback_entities(
        self,
        batch_id: str,
        snapshot_ids: Optional[List[int]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        """Roll back (rejoin) previously left entities from a cleanup batch.

        Rejoins public entities with usernames via `JoinChannelRequest`,
        marks private entities as `unrestorable_private`, and updates audit logs.

        Args:
            batch_id: Cleanup batch ID to rollback from.
            snapshot_ids: Optional list of snapshot IDs or entity IDs for selective rollback.
            progress_callback: Optional callback receiving progress event dicts.

        Returns:
            Dict containing batch_id, total, restored, unrestorable_private, failed,
            and updated backup file paths.
        """
        client = await self._ensure_connected()

        # 1. Fetch snapshots from DB
        all_snapshots = self.storage.get_batch_snapshots(batch_id)
        if snapshot_ids is not None:
            id_set = set(snapshot_ids)
            target_snapshots = [
                s for s in all_snapshots if (s.get("id") in id_set or s.get("entity_id") in id_set)
            ]
        else:
            target_snapshots = all_snapshots

        restored_count = 0
        unrestorable_count = 0
        failed_count = 0

        for snapshot in target_snapshots:
            snap_id = snapshot["id"]
            title = snapshot.get("title", "")
            raw_username = snapshot.get("username")
            username = str(raw_username).strip() if raw_username else None
            is_private = bool(snapshot.get("is_private", False))

            # Case A: Private entity without public username (unrestorable via API)
            if is_private and not username:
                err_msg = "Приватный канал без публичного юзернейма. Автоматический возврат невозможен."
                self.storage.update_snapshot_restore_status(
                    snapshot_id=snap_id,
                    status=RestoreStatus.UNRESTORABLE_PRIVATE.value,
                    error=err_msg,
                )
                unrestorable_count += 1
                await _emit_callback(
                    progress_callback,
                    {
                        "entity": title,
                        "status": RestoreStatus.UNRESTORABLE_PRIVATE.value,
                        "error": err_msg,
                    },
                )
                continue

            # Case B: Entity lacking username (even if flagged public)
            if not username:
                err_msg = "Канал/чат без публичного юзернейма. Автоматический возврат невозможен."
                self.storage.update_snapshot_restore_status(
                    snapshot_id=snap_id,
                    status=RestoreStatus.UNRESTORABLE_PRIVATE.value,
                    error=err_msg,
                )
                unrestorable_count += 1
                await _emit_callback(
                    progress_callback,
                    {
                        "entity": title,
                        "status": RestoreStatus.UNRESTORABLE_PRIVATE.value,
                        "error": err_msg,
                    },
                )
                continue

            # Case C: Public entity with username -> attempt rejoin
            delay = random.uniform(*self.rollback_delay_range)
            await asyncio.sleep(delay)

            rejoin_success = False
            last_error = None
            max_retries = 3

            for attempt in range(max_retries):
                try:
                    clean_username = username.lstrip("@")
                    await client(functions.channels.JoinChannelRequest(channel=clean_username))
                    rejoin_success = True
                    break

                except errors.FloodWaitError as e:
                    last_error = e
                    logger.warning("FloodWaitError on rollback for %s: sleeping %d seconds", title, e.seconds)
                    await _emit_callback(
                        progress_callback,
                        {
                            "status": "flood_wait",
                            "wait_seconds": e.seconds,
                            "entity": title,
                        },
                    )
                    await asyncio.sleep(e.seconds + 1)

                except errors.UserAlreadyParticipantError:
                    # User is already in the channel
                    rejoin_success = True
                    break

                except Exception as e:
                    last_error = e
                    logger.error("Error rejoining %s: %s", username, e)
                    break

            if rejoin_success:
                restored_at_iso = datetime.now(timezone.utc).isoformat()
                self.storage.update_snapshot_restore_status(
                    snapshot_id=snap_id,
                    status=RestoreStatus.RESTORED.value,
                    restored_at=restored_at_iso,
                    error=None,
                )
                restored_count += 1
                await _emit_callback(
                    progress_callback,
                    {
                        "entity": title,
                        "status": RestoreStatus.RESTORED.value,
                    },
                )
            else:
                self.storage.update_snapshot_restore_status(
                    snapshot_id=snap_id,
                    status=RestoreStatus.FAILED.value,
                    error=str(last_error),
                )
                failed_count += 1
                await _emit_callback(
                    progress_callback,
                    {
                        "entity": title,
                        "status": RestoreStatus.FAILED.value,
                        "error": str(last_error),
                    },
                )

        # 2. Re-export backup JSON and CSV with updated restore statuses
        updated_snapshots = self.storage.get_batch_snapshots(batch_id)
        json_path, csv_path = export_batch_backup(
            batch_id=batch_id,
            snapshots=updated_snapshots,
            backup_dir=self.backup_dir,
        )

        return {
            "batch_id": batch_id,
            "total": len(target_snapshots),
            "restored": restored_count,
            "unrestorable_private": unrestorable_count,
            "failed": failed_count,
            "backup_json": json_path,
            "backup_csv": csv_path,
        }

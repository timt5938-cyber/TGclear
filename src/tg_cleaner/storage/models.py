"""Domain models and data structures for Telegram Cleaner.

Contains Pydantic models for chat entities, filter configurations,
audit snapshot records, and cleanup batches.
"""

from enum import Enum
from typing import List, Optional, Union
from pydantic import BaseModel, Field, ConfigDict


class EntityType(str, Enum):
    """Supported Telegram entity types for channels and groups."""
    CHANNEL = "channel"
    SUPERGROUP = "supergroup"
    GROUP = "group"


class RestoreStatus(str, Enum):
    """Lifecycle status of departed entities regarding rollback/restoration."""
    PENDING = "pending"
    LEFT = "left"
    RESTORED = "restored"
    UNRESTORABLE_PRIVATE = "unrestorable_private"
    FAILED = "failed"


class ChatEntity(BaseModel):
    """Represents a Telegram dialog candidate/entity discovered during scan."""
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    id: int
    title: str
    username: Optional[str] = None
    entity_type: str  # 'channel', 'supergroup', 'group'
    is_private: bool
    is_protected: bool = False  # True if admin/creator, pinned, or in whitelist
    is_pinned: bool = False
    is_creator: bool = False
    is_admin: bool = False
    is_archived: bool = False
    is_folder_protected: bool = False
    is_recent_read_protected: bool = False
    folders: List[str] = Field(default_factory=list)
    folder_ids: List[int] = Field(default_factory=list)
    unread_count: int = 0
    dormancy_days: int = 0  # Days since last message in channel/group
    user_inactive_days: int = 0  # Days since user last read incoming messages
    trigger_reasons: List[str] = Field(default_factory=list)
    is_candidate: bool = False
    selected: bool = False


class FilterConfig(BaseModel):
    """Configuration thresholds and logical toggles for candidate filtering."""
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    user_inactive_days: int = 60
    unread_threshold: int = 200
    dormancy_days: int = 90
    logic_mode: str = "ANY"  # 'ANY' or 'ALL'
    protect_pinned: bool = True
    protect_admin: bool = True
    protect_recent_read: bool = False
    protect_recent_read_days: int = 2  # Не удалять, если не читал менее N дней назад (например, 2 дня)
    ignore_archived: bool = True  # Игнорировать чаты из архива
    protected_folder_ids: List[int] = Field(default_factory=list)  # Папки Телеграма, защищенные от удаления
    filter_user_inactive_enabled: bool = True
    filter_unread_enabled: bool = True
    filter_dormancy_enabled: bool = True


class SnapshotRecord(BaseModel):
    """Persistent audit record of an entity captured immediately prior to departure."""
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    id: Optional[int] = None  # Database primary key auto-increment
    batch_id: str
    entity_id: int
    title: str
    username: Optional[str] = None
    is_private: bool
    entity_type: str
    unread_count: int = 0
    dormancy_days: int = 0
    user_inactive_days: int = 0
    trigger_reasons: str = ""  # Comma-separated or JSON string
    left_at: str
    restore_status: str = RestoreStatus.LEFT.value  # 'pending', 'left', 'restored', 'unrestorable_private', 'failed'
    restored_at: Optional[str] = None
    error_message: Optional[str] = None


class CleanupBatch(BaseModel):
    """Summary of a discrete cleanup run that departed from confirmed candidates."""
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    id: str
    created_at: str
    total_candidates: int
    departed_count: int = 0
    restored_count: int = 0
    notes: Optional[str] = None

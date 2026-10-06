"""Core modules for Telegram Cleaner."""

from tg_cleaner.core.filters import evaluate_candidates, evaluate_entity
from tg_cleaner.core.telegram_service import (
    MockDialog,
    MockEntity,
    MockTelethonClient,
    TelegramService,
)

__all__ = [
    "evaluate_candidates",
    "evaluate_entity",
    "TelegramService",
    "MockTelethonClient",
    "MockDialog",
    "MockEntity",
]

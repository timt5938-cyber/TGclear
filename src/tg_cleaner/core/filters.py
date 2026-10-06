"""Candidate filtering and protection evaluation engine.

Implements rules for protecting critical Telegram channels/groups
(whitelisted, admin/creator, pinned) and evaluating inactivity/unread
thresholds to identify departure candidates.
"""

from typing import List, Set
from tg_cleaner.storage.models import ChatEntity, FilterConfig


def evaluate_entity(
    entity: ChatEntity,
    config: FilterConfig,
    whitelist_ids: Set[int],
) -> ChatEntity:
    """Evaluate protection rules and departure filter thresholds for a single entity.
    
    Mutates and returns the entity with updated `is_protected`, `is_candidate`,
    `selected`, and `trigger_reasons`.
    """
    # 1. Protection evaluation
    in_whitelist = entity.id in whitelist_ids
    is_admin_or_creator = config.protect_admin and (entity.is_creator or entity.is_admin)
    is_pinned_protected = config.protect_pinned and entity.is_pinned
    is_archived_ignored = config.ignore_archived and entity.is_archived
    is_recent_read_protected = config.protect_recent_read and (entity.user_inactive_days <= config.protect_recent_read_days)
    is_folder_protected = bool(config.protected_folder_ids and any(fid in config.protected_folder_ids for fid in entity.folder_ids))

    entity.is_folder_protected = is_folder_protected
    entity.is_recent_read_protected = is_recent_read_protected

    if in_whitelist or is_admin_or_creator or is_pinned_protected or is_archived_ignored or is_recent_read_protected or is_folder_protected:
        entity.is_protected = True
        entity.is_candidate = False
        entity.selected = False
        entity.trigger_reasons = []
        return entity

    entity.is_protected = False

    # 2. Evaluate active criteria
    matched_reasons: List[str] = []
    total_active_filters = 0
    total_matched_filters = 0

    if config.filter_user_inactive_enabled:
        total_active_filters += 1
        if entity.user_inactive_days >= config.user_inactive_days:
            total_matched_filters += 1
            matched_reasons.append(
                f"Не читал {entity.user_inactive_days} дн. (порог {config.user_inactive_days} дн.)"
            )

    if config.filter_unread_enabled:
        total_active_filters += 1
        if entity.unread_count >= config.unread_threshold:
            total_matched_filters += 1
            matched_reasons.append(
                f"Непрочитанных {entity.unread_count} (порог {config.unread_threshold})"
            )

    if config.filter_dormancy_enabled:
        total_active_filters += 1
        if entity.dormancy_days >= config.dormancy_days:
            total_matched_filters += 1
            matched_reasons.append(
                f"Нет постов {entity.dormancy_days} дн. (порог {config.dormancy_days} дн.)"
            )

    # 3. Determine candidate status based on logic_mode ('ANY' vs 'ALL')
    mode = (config.logic_mode or "ANY").strip().upper()

    is_candidate = False
    if total_active_filters > 0:
        if mode == "ALL":
            is_candidate = total_matched_filters == total_active_filters
        else:  # Default to 'ANY'
            is_candidate = total_matched_filters > 0

    entity.is_candidate = is_candidate
    if is_candidate:
        entity.selected = True
        entity.trigger_reasons = matched_reasons
    else:
        entity.selected = False
        entity.trigger_reasons = []

    return entity


def evaluate_candidates(
    entities: List[ChatEntity],
    config: FilterConfig,
    whitelist_ids: Set[int],
) -> List[ChatEntity]:
    """Evaluate a collection of entities against filter criteria and whitelist.

    Args:
        entities: List of chat entities to evaluate.
        config: Thresholds, enabled filters, and combination mode (ANY/ALL).
        whitelist_ids: Set of Telegram entity IDs protected from departure.

    Returns:
        List of evaluated ChatEntity objects with updated status flags.
    """
    for entity in entities:
        evaluate_entity(entity, config, whitelist_ids)
    return entities

"""Backup export and inspection for Telegram Cleaner cleanup batches.

Generates persistent JSON audit dumps (with metadata summary) and CSV exports
suitable for user inspection in spreadsheet software (Excel, LibreOffice)
and selective manual restoration.
"""

import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_BACKUP_DIR = r"C:\tg\backups"

CSV_HEADERS = [
    "ID",
    "Title",
    "Username",
    "Type",
    "Private",
    "Unread",
    "DormancyDays",
    "UserInactiveDays",
    "TriggerReason",
    "LeftAt",
    "Status",
]


def _format_trigger_reasons(reasons: Any) -> str:
    """Format trigger reasons into a clean string representation for CSV output."""
    if isinstance(reasons, list):
        return "; ".join(str(r) for r in reasons)
    if isinstance(reasons, str):
        # Check if it is a JSON serialized list
        try:
            parsed = json.loads(reasons)
            if isinstance(parsed, list):
                return "; ".join(str(r) for r in parsed)
        except Exception:
            pass
        return reasons
    return str(reasons or "")


def export_batch_backup(
    batch_id: str,
    snapshots: List[Dict[str, Any]],
    backup_dir: str = DEFAULT_BACKUP_DIR,
) -> Tuple[str, str]:
    """Export snapshot records of a cleanup batch into both JSON and CSV files.

    Args:
        batch_id: Unique identifier of the cleanup batch.
        snapshots: List of dictionary representations of SnapshotRecord objects.
        backup_dir: Destination directory path for backup files.

    Returns:
        A tuple of file paths: (json_path, csv_path).
    """
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # 1. Prepare JSON structure with rich metadata
    by_type: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    private_count = 0
    public_count = 0

    for s in snapshots:
        e_type = s.get("entity_type", "unknown")
        by_type[e_type] = by_type.get(e_type, 0) + 1

        status = s.get("restore_status", "left")
        by_status[status] = by_status.get(status, 0) + 1

        if s.get("is_private"):
            private_count += 1
        else:
            public_count += 1

    json_payload = {
        "metadata": {
            "batch_id": batch_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total_entities": len(snapshots),
            "public_count": public_count,
            "private_count": private_count,
            "breakdown_by_type": by_type,
            "breakdown_by_status": by_status,
        },
        "snapshots": snapshots,
    }

    json_filename = f"cleanup_batch_{batch_id}_{timestamp}.json"
    json_path = os.path.join(backup_dir, json_filename)
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(json_payload, jf, indent=2, ensure_ascii=False)

    # 2. Prepare CSV export
    csv_filename = f"cleanup_batch_{batch_id}_{timestamp}.csv"
    csv_path = os.path.join(backup_dir, csv_filename)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow(CSV_HEADERS)
        for s in snapshots:
            writer.writerow([
                s.get("entity_id", s.get("id", "")),
                s.get("title", ""),
                s.get("username") or "",
                s.get("entity_type", ""),
                s.get("is_private", False),
                s.get("unread_count", 0),
                s.get("dormancy_days", 0),
                s.get("user_inactive_days", 0),
                _format_trigger_reasons(s.get("trigger_reasons", "")),
                s.get("left_at", ""),
                s.get("restore_status", "left"),
            ])

    return (json_path, csv_path)


def list_backups(backup_dir: str = DEFAULT_BACKUP_DIR) -> List[Dict[str, Any]]:
    """List all available backup export files sorted with the newest first.

    Args:
        backup_dir: Directory containing backup files.

    Returns:
        List of dictionaries with file metadata: filename, filepath, size_bytes,
        size_kb, created_at, modified_at, file_type, and parsed batch_id.
    """
    if not os.path.exists(backup_dir):
        return []

    backups: List[Dict[str, Any]] = []
    batch_pattern = re.compile(r"^cleanup_batch_(.+?)_\d{8}_\d{6}\.(json|csv)$")

    for entry in os.scandir(backup_dir):
        if not entry.is_file():
            continue

        ext = Path(entry.name).suffix.lstrip(".").lower()
        if ext not in ("json", "csv"):
            continue

        stat = entry.stat()
        created_dt = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        modified_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        match = batch_pattern.match(entry.name)
        batch_id = match.group(1) if match else None

        backups.append({
            "filename": entry.name,
            "filepath": entry.path,
            "size_bytes": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 2),
            "created_at": created_dt.isoformat(),
            "modified_at": modified_dt.isoformat(),
            "file_type": ext,
            "batch_id": batch_id,
        })

    backups.sort(key=lambda b: b["created_at"], reverse=True)
    return backups

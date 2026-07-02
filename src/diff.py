"""
diff.py — Compare fresh PO data against last snapshot
Detects: new POs, cancelled POs, updated PO fields
"""

import json
import os
from datetime import datetime
from pathlib import Path
from src.logger import get_logger

log = get_logger()

SNAPSHOTS_DIR = Path("snapshots")


# ─────────────────────────────────────────────────────────────
#  SNAPSHOT MANAGEMENT
# ─────────────────────────────────────────────────────────────

def save_snapshot(pos: list[dict], customer_id: str) -> Path:
    """Save current PO list as a timestamped JSON snapshot."""
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SNAPSHOTS_DIR / f"{customer_id}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pos, f, indent=2, ensure_ascii=False)
    log.debug(f"Snapshot saved → {path}")
    return path


def load_latest_snapshot(customer_id: str) -> dict:
    """
    Load the most recent snapshot for a customer.
    Returns a dict keyed by po_number, or {} if no snapshot exists.
    """
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    files = sorted(SNAPSHOTS_DIR.glob(f"{customer_id}_*.json"))

    if not files:
        log.info(f"No previous snapshot found for {customer_id} (first run)")
        return {}

    latest = files[-1]
    log.debug(f"Loading snapshot: {latest}")
    with open(latest, "r", encoding="utf-8") as f:
        pos = json.load(f)

    return _index_by_po_number(pos)


def _index_by_po_number(pos: list[dict]) -> dict:
    """Convert list of POs to dict keyed by po_number."""
    indexed = {}
    for po in pos:
        key = str(po.get("po_number", "")).strip()
        if key:
            indexed[key] = po
    return indexed


# ─────────────────────────────────────────────────────────────
#  DIFF ENGINE
# ─────────────────────────────────────────────────────────────

def detect_changes(
    fresh_pos: list[dict],
    customer_id: str,
    config: dict,
) -> dict:
    """
    Compare fresh POs against the last snapshot.
    Returns dict with keys: new, cancelled, updated, unchanged
    """
    old_snapshot = load_latest_snapshot(customer_id)
    new_snapshot  = _index_by_po_number(fresh_pos)

    old_keys = set(old_snapshot.keys())
    new_keys  = set(new_snapshot.keys())

    # ── New POs ──
    new_po_keys = new_keys - old_keys
    new_pos = [new_snapshot[k] for k in new_po_keys]

    # ── Cancelled POs ──
    # A PO is "cancelled" if it disappeared OR its status changed to cancelled
    disappeared_keys = old_keys - new_keys
    cancelled_pos = [old_snapshot[k] for k in disappeared_keys]

    # Also check for status-based cancellation
    for key in old_keys & new_keys:
        new_status = str(new_snapshot[key].get("status", "")).lower()
        old_status = str(old_snapshot[key].get("status", "")).lower()
        cancelled_keywords = ["cancel", "reject", "void", "closed", "terminated"]
        if (
            any(kw in new_status for kw in cancelled_keywords)
            and not any(kw in old_status for kw in cancelled_keywords)
        ):
            cancelled_pos.append(new_snapshot[key])

    # ── Updated POs ──
    updated_pos = []
    skip_fields = {"last_synced", "raw"}          # Don't diff meta fields
    alert_pct   = config.get("validation", {}).get("amount_change_alert_pct", 20)

    for key in old_keys & new_keys:
        old = old_snapshot[key]
        new = new_snapshot[key]
        field_changes = []

        for field in set(list(old.keys()) + list(new.keys())):
            if field in skip_fields:
                continue
            old_val = str(old.get(field, "")).strip()
            new_val = str(new.get(field, "")).strip()
            if old_val != new_val:
                change = {
                    "field": field,
                    "old": old_val,
                    "new": new_val,
                }
                # Flag large amount changes
                if field == "amount":
                    change["large_change"] = _is_large_change(
                        old_val, new_val, alert_pct
                    )
                field_changes.append(change)

        if field_changes:
            updated_pos.append({
                "po_number": key,
                "current": new,
                "changes": field_changes,
            })

    unchanged = len(new_keys) - len(new_po_keys) - len(updated_pos)

    log.info(
        f"🔍 Diff result — "
        f"New: {len(new_pos)} | "
        f"Cancelled: {len(cancelled_pos)} | "
        f"Updated: {len(updated_pos)} | "
        f"Unchanged: {unchanged}"
    )

    return {
        "new":       new_pos,
        "cancelled": cancelled_pos,
        "updated":   updated_pos,
        "unchanged": unchanged,
        "total":     len(new_pos),
        "run_at":    datetime.now().isoformat(),
    }


def _is_large_change(old_val: str, new_val: str, threshold_pct: float) -> bool:
    """Returns True if amount changed by more than threshold_pct %."""
    try:
        # Strip currency symbols and commas
        import re
        clean = lambda s: float(re.sub(r"[^\d.]", "", s) or "0")
        old_num = clean(old_val)
        new_num = clean(new_val)
        if old_num == 0:
            return False
        return abs((new_num - old_num) / old_num * 100) > threshold_pct
    except Exception:
        return False

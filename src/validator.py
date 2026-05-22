"""
validator.py — Accuracy validation before writing to Google Sheets
Quarantines records that fail validation instead of writing bad data
"""

import re
import json
from datetime import datetime
from pathlib import Path
from src.logger import get_logger

log = get_logger()

QUARANTINE_DIR = Path("logs/quarantine")


def validate_po_list(pos: list[dict], config: dict) -> tuple[list[dict], list[dict]]:
    """
    Validate all POs against config rules.
    Returns (valid_pos, quarantined_pos)
    """
    valid = []
    quarantined = []

    rules = config.get("validation", {})
    po_pattern     = rules.get("po_number_pattern", ".*")
    amount_min     = float(rules.get("amount_min", 0))
    required       = rules.get("required_fields", ["po_number"])

    for po in pos:
        errors = _validate_single(po, po_pattern, amount_min, required)
        if errors:
            quarantined.append({"po": po, "errors": errors})
            log.warning(
                f"⚠️  Quarantined PO {po.get('po_number','?')}: {'; '.join(errors)}"
            )
        else:
            valid.append(po)

    if quarantined:
        _save_quarantine(quarantined, config["customer_id"])

    log.info(f"✅ Validation — Passed: {len(valid)} | Quarantined: {len(quarantined)}")
    return valid, quarantined


def _validate_single(
    po: dict,
    po_pattern: str,
    amount_min: float,
    required_fields: list,
) -> list[str]:
    """Returns list of error strings. Empty = valid."""
    errors = []

    # Required fields present
    for field in required_fields:
        val = str(po.get(field, "")).strip()
        if not val:
            errors.append(f"Missing required field: '{field}'")

    # PO number matches expected pattern
    po_number = str(po.get("po_number", "")).strip()
    if po_number and po_pattern and po_pattern != ".*":
        if not re.fullmatch(po_pattern, po_number):
            errors.append(
                f"PO number '{po_number}' doesn't match pattern '{po_pattern}'"
            )

    # Amount is numeric and above minimum
    amount_str = str(po.get("amount", "")).strip()
    if amount_str:
        try:
            cleaned = re.sub(r"[^\d.]", "", amount_str)
            amount  = float(cleaned) if cleaned else 0.0
            if amount < amount_min:
                errors.append(
                    f"Amount {amount} is below minimum {amount_min}"
                )
        except ValueError:
            errors.append(f"Amount '{amount_str}' is not numeric")

    # Dates parse if present
    for date_field in ["po_date", "delivery_date"]:
        date_str = str(po.get(date_field, "")).strip()
        if date_str:
            if not _is_parseable_date(date_str):
                errors.append(f"'{date_field}' value '{date_str}' is not a recognisable date")

    return errors


def _is_parseable_date(s: str) -> bool:
    formats = [
        "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y",
        "%d %b %Y", "%d %B %Y", "%Y/%m/%d",
        "%d/%m/%y", "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    # Accept if it's a non-empty string with at least 4 digits (loose fallback)
    return bool(re.search(r"\d{4}", s))


def _save_quarantine(records: list[dict], customer_id: str):
    """Persist quarantined records for manual review."""
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = QUARANTINE_DIR / f"{customer_id}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    log.info(f"🗂  Quarantine saved → {path}")

"""
sheets.py — Write PO changes to Google Sheets
Columns: A=Date | B=PO Number | C=Status | D=Update Info | E=Category
Status labels: New Issued | Updated | Removed
Text: always black, readable
"""

import os
from datetime import datetime
from src.logger import get_logger

log = get_logger()

# ── Row colours (pastel — light enough for BLACK text) ──
COLOR_NEW       = {"red": 0.78, "green": 0.91, "blue": 0.76}   # Soft green
COLOR_UPDATED   = {"red": 1.00, "green": 0.95, "blue": 0.71}   # Soft amber
COLOR_REMOVED   = {"red": 0.95, "green": 0.78, "blue": 0.78}   # Soft red
COLOR_HEADER    = {"red": 0.18, "green": 0.18, "blue": 0.18}   # Dark grey
COLOR_WHITE     = {"red": 1.00, "green": 1.00, "blue": 1.00}   # White
COLOR_BLACK     = {"red": 0.00, "green": 0.00, "blue": 0.00}   # Black text

HEADERS = ["Date", "PO Number", "Status", "Update Info", "Category"]

# Fields we care about for update_info — human readable labels
FIELD_LABELS = {
    "status":        "Status",
    "delivery_date": "Delivery Date",
    "po_date":       "PO Date",
    "amount":        "Amount",
    "vendor":        "Vendor",
    "qty":           "Quantity",
    "days":          "Days",
    "remarks":       "Remarks",
    "article":       "Article",
}

# Fields to ignore in update_info (too noisy / not useful)
IGNORE_FIELDS = {"last_synced", "raw", "category", "currency",
                 "vendor_code", "risk", "line_items"}


def get_sheets_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./service-account.json")
    if not os.path.exists(sa_file):
        raise FileNotFoundError(f"service-account.json not found at: {sa_file}")

    creds = Credentials.from_service_account_file(
        sa_file,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def clear_and_reset_sheet(service, spreadsheet_id: str, tab_name: str):
    """
    Wipe all existing data and old columns, then write fresh headers.
    Call this once to clean up the messy sheet.
    """
    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)

    # Clear entire sheet content
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A:Z",
    ).execute()

    # Also clear all background formatting
    if sheet_id is not None:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId":          sheet_id,
                        "startRowIndex":    0,
                        "endRowIndex":      1000,
                        "startColumnIndex": 0,
                        "endColumnIndex":   26,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": COLOR_WHITE,
                            "textFormat": {
                                "foregroundColor": COLOR_BLACK,
                                "bold": False,
                            }
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            }]},
        ).execute()

    log.info(f"🧹 Sheet '{tab_name}' cleared")

    # Write clean headers
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()

    if sheet_id is not None:
        _format_header_row(service, spreadsheet_id, sheet_id)
        _set_column_widths(service, spreadsheet_id, sheet_id)

    log.info(f"✅ Sheet reset with clean headers: {HEADERS}")


def ensure_sheet_headers(service, spreadsheet_id: str, tab_name: str, col_map: dict):
    """Create tab if missing and write headers."""
    meta   = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]

    if tab_name not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
        ).execute()
        log.info(f"📄 Created tab: {tab_name}")

    result   = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1:E1",
    ).execute()
    existing = result.get("values", [[]])[0]

    if existing == HEADERS:
        log.debug("Headers already correct")
        return

    # Headers wrong or missing — reset the sheet cleanly
    log.info("Headers outdated — resetting sheet...")
    clear_and_reset_sheet(service, spreadsheet_id, tab_name)


def write_changes_to_sheet(changes: dict, config: dict):
    """Main entry — write all PO changes to the Google Sheet."""
    sheet_id = config["sheet_id"]
    tab_name = config["google_sheet"]["tab_name"]
    col_map  = config["google_sheet"]["columns"]
    customer = config["customer_name"]

    if not sheet_id or sheet_id == "YOUR_GOOGLE_SHEET_ID_HERE":
        log.warning(f"⚠️  Sheet ID not set for {customer}")
        _log_changes_locally(changes, config)
        return

    try:
        service = get_sheets_service()
    except FileNotFoundError as e:
        log.warning(f"⚠️  {e}")
        _log_changes_locally(changes, config)
        return

    ensure_sheet_headers(service, sheet_id, tab_name, col_map)
    existing_data = _load_sheet_data(service, sheet_id, tab_name)
    summary = {"new": 0, "updated": 0, "removed": 0}

    # ── New Issued ──────────────────────────────────────────
    for po in changes.get("new", []):
        row = _build_row(
            po_number  = po.get("po_number", ""),
            status     = "New Issued",
            update_info= _new_po_summary(po),
            category   = po.get("category", ""),
        )
        _append_row(service, sheet_id, tab_name, row)
        existing_data = _load_sheet_data(service, sheet_id, tab_name)
        _colour_last_row(service, sheet_id, tab_name, COLOR_NEW, existing_data)
        summary["new"] += 1

    # ── Updated ─────────────────────────────────────────────
    for upd in changes.get("updated", []):
        update_info = _format_update_info(upd.get("changes", []))
        po          = upd.get("current", {})
        row_idx     = _find_row(existing_data, upd["po_number"])

        row = _build_row(
            po_number  = upd["po_number"],
            status     = "Updated",
            update_info= update_info,
            category   = po.get("category", ""),
        )

        if row_idx:
            _update_row(service, sheet_id, tab_name, row_idx, row)
            _colour_row(service, sheet_id, tab_name, row_idx, COLOR_UPDATED)
        else:
            _append_row(service, sheet_id, tab_name, row)
            existing_data = _load_sheet_data(service, sheet_id, tab_name)
            _colour_last_row(service, sheet_id, tab_name, COLOR_UPDATED, existing_data)

        summary["updated"] += 1

    # ── Removed ─────────────────────────────────────────────
    for po in changes.get("cancelled", []):
        row_idx = _find_row(existing_data, po.get("po_number", ""))

        row = _build_row(
            po_number  = po.get("po_number", ""),
            status     = "Removed",
            update_info= "PO removed from portal",
            category   = po.get("category", ""),
        )

        if row_idx:
            _update_row(service, sheet_id, tab_name, row_idx, row)
            _colour_row(service, sheet_id, tab_name, row_idx, COLOR_REMOVED)
        else:
            _append_row(service, sheet_id, tab_name, row)
            existing_data = _load_sheet_data(service, sheet_id, tab_name)
            _colour_last_row(service, sheet_id, tab_name, COLOR_REMOVED, existing_data)

        summary["removed"] += 1

    # Apply black text to all data rows
    _set_data_text_black(service, sheet_id, tab_name, len(existing_data))

    log.info(
        f"✅ Sheet sync done — "
        f"New: {summary['new']} | "
        f"Updated: {summary['updated']} | "
        f"Removed: {summary['removed']}"
    )


# ─────────────────────────────────────────────────────────────
#  UPDATE INFO FORMATTING — clean, human readable
# ─────────────────────────────────────────────────────────────

def _new_po_summary(po: dict) -> str:
    """Short summary for a newly issued PO."""
    parts = []
    if po.get("vendor") or po.get("vendor_name"):
        parts.append(po.get("vendor") or po.get("vendor_name"))
    if po.get("amount"):
        parts.append(f"₹{po['amount']}")
    if po.get("delivery_date"):
        parts.append(f"Delivery: {po['delivery_date']}")
    return " | ".join(parts) if parts else "New PO issued"


def _format_update_info(changes: list) -> str:
    """
    Convert raw field diffs into clean, short, human-readable text.
    Example: "Status: Barcoding → Packed | Delivery: 27 Jul → 3 Oct"
    """
    parts = []
    for c in changes:
        field = c.get("field", "")
        if field in IGNORE_FIELDS:
            continue

        label   = FIELD_LABELS.get(field, field.replace("_", " ").title())
        old_val = _clean_value(c.get("old", ""))
        new_val = _clean_value(c.get("new", ""))

        if not old_val and not new_val:
            continue

        if c.get("large_change"):
            parts.append(f"{label}: {old_val} → {new_val} ⚠️")
        else:
            parts.append(f"{label}: {old_val} → {new_val}")

    return " | ".join(parts) if parts else "Fields updated"


def _clean_value(val: str) -> str:
    """Strip noisy suffixes like '1d ago', '26d ago' from status values."""
    import re
    if not val:
        return ""
    # Remove trailing " Xd ago", " X days ago", "+Xd" etc.
    cleaned = re.sub(r'\s+\d+d?\s+ago$', '', val.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\+\d+d$', '', cleaned.strip())
    return cleaned.strip()


# ─────────────────────────────────────────────────────────────
#  ROW BUILDER
# ─────────────────────────────────────────────────────────────

def _build_row(po_number: str, status: str, update_info: str, category: str) -> list:
    return [
        datetime.now().strftime("%d-%m-%Y"),
        po_number,
        status,
        update_info,
        _normalise_category(category),
    ]


def _normalise_category(raw: str) -> str:
    if not raw:
        return ""
    r = raw.lower()
    if "girl" in r:
        return "Girls"
    if "women" in r or "woman" in r or "ladies" in r:
        return "Women"
    return raw.title()


# ─────────────────────────────────────────────────────────────
#  SHEET OPERATIONS
# ─────────────────────────────────────────────────────────────

def _append_row(service, spreadsheet_id, tab_name, row):
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A:E",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def _update_row(service, spreadsheet_id, tab_name, row_idx, row):
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A{row_idx}:E{row_idx}",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()


def _find_row(sheet_data, po_number) -> int | None:
    """Find 1-based row index by PO number in column B (index 1)."""
    if not po_number or not sheet_data:
        return None
    for idx, row in enumerate(sheet_data):
        if idx == 0:
            continue
        if len(row) > 1 and str(row[1]).strip() == str(po_number).strip():
            return idx + 1
    return None


def _load_sheet_data(service, spreadsheet_id, tab_name):
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{tab_name}!A:E",
        ).execute()
        return result.get("values", [])
    except Exception as e:
        log.debug(f"Could not load sheet: {e}")
        return []


def _colour_row(service, spreadsheet_id, tab_name, row_idx, color):
    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    if sheet_id is not None:
        _highlight_row(service, spreadsheet_id, sheet_id, row_idx, color)


def _colour_last_row(service, spreadsheet_id, tab_name, color, existing_data):
    last_idx = len(existing_data)
    if last_idx > 1:
        _colour_row(service, spreadsheet_id, tab_name, last_idx, color)


def _highlight_row(service, spreadsheet_id, sheet_id, row_idx, color):
    """Set background colour AND ensure black text."""
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId":          sheet_id,
                    "startRowIndex":    row_idx - 1,
                    "endRowIndex":      row_idx,
                    "startColumnIndex": 0,
                    "endColumnIndex":   5,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": color,
                        "textFormat": {
                            "foregroundColor": COLOR_BLACK,
                            "bold": False,
                        }
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        }]},
    ).execute()


def _set_data_text_black(service, spreadsheet_id, tab_name, num_rows):
    """Force all data rows (row 2 onwards) to have black text."""
    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    if sheet_id is None or num_rows < 2:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId":          sheet_id,
                    "startRowIndex":    1,
                    "endRowIndex":      max(num_rows + 5, 100),
                    "startColumnIndex": 0,
                    "endColumnIndex":   5,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "foregroundColor": COLOR_BLACK,
                        }
                    }
                },
                "fields": "userEnteredFormat.textFormat.foregroundColor",
            }
        }]},
    ).execute()


def _format_header_row(service, spreadsheet_id, sheet_id):
    """Bold white text on dark background, frozen."""
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex":   1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": COLOR_HEADER,
                            "textFormat": {
                                "bold": True,
                                "foregroundColor": COLOR_WHITE,
                            },
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
        ]},
    ).execute()


def _set_column_widths(service, spreadsheet_id, sheet_id):
    """Set sensible column widths for readability."""
    widths = [
        (0, 100),   # A: Date
        (1, 140),   # B: PO Number
        (2, 110),   # C: Status
        (3, 420),   # D: Update Info (widest)
        (4, 100),   # E: Category
    ]
    requests = [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId":    sheet_id,
                    "dimension":  "COLUMNS",
                    "startIndex": col,
                    "endIndex":   col + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        }
        for col, width in widths
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def _get_sheet_id(service, spreadsheet_id, tab_name):
    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in meta["sheets"]:
            if sheet["properties"]["title"] == tab_name:
                return sheet["properties"]["sheetId"]
    except Exception as e:
        log.debug(f"Could not get sheet ID: {e}")
    return None


def _log_changes_locally(changes, config):
    import json
    from pathlib import Path
    Path("logs").mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(f"logs/{config['customer_id']}_changes_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(changes, f, indent=2, ensure_ascii=False, default=str)
    log.info(f"📁 Changes saved locally → {path}")

"""
sheets.py — Write PO changes to Google Sheets
Columns: Date | PO Number | Status | Update Info | Category
Status values: New Issued | Updated | Removed
"""

import os
from datetime import datetime
from src.logger import get_logger

log = get_logger()

# ── Row colours ──
COLOR_NEW       = {"red": 0.85, "green": 0.93, "blue": 0.83}   # Light green
COLOR_CANCELLED = {"red": 0.96, "green": 0.80, "blue": 0.80}   # Light red
COLOR_UPDATED   = {"red": 1.00, "green": 0.95, "blue": 0.80}   # Light amber
COLOR_HEADER    = {"red": 0.13, "green": 0.13, "blue": 0.13}   # Dark
COLOR_WHITE     = {"red": 1.00, "green": 1.00, "blue": 1.00}

# ── Fixed headers ──
HEADERS = ["Date", "PO Number", "Status", "Update Info", "Category"]


def get_sheets_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./service-account.json")
    if not os.path.exists(sa_file):
        raise FileNotFoundError(
            f"service-account.json not found at: {sa_file}\n"
            f"Place the file in your project folder."
        )
    creds = Credentials.from_service_account_file(
        sa_file,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def ensure_sheet_headers(service, spreadsheet_id: str, tab_name: str, col_map: dict):
    """Create tab if missing and write headers. Safe to call every run."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]

    if tab_name not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
        ).execute()
        log.info(f"📄 Created tab: {tab_name}")

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1:E1",
    ).execute()
    existing = result.get("values", [[]])[0]

    if existing == HEADERS:
        log.debug("Headers already correct")
        return

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]},
    ).execute()

    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    if sheet_id is not None:
        _format_header_row(service, spreadsheet_id, sheet_id)

    log.info(f"✅ Headers written: {HEADERS}")


def write_changes_to_sheet(changes: dict, config: dict):
    """Main entry point — write all PO changes to the sheet."""
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
    summary = {"new": 0, "updated": 0, "cancelled": 0}

    # ── New POs ──
    for po in changes.get("new", []):
        row = _build_row(
            po_number=po.get("po_number", ""),
            status="New Issued",
            update_info=f"PO issued — {po.get('vendor') or po.get('vendor_name', '')}".strip(" —"),
            category=po.get("category", ""),
        )
        _append_row(service, sheet_id, tab_name, row)
        _colour_last_row(service, sheet_id, tab_name, COLOR_NEW, existing_data)
        summary["new"] += 1

    # ── Updated POs ──
    for upd in changes.get("updated", []):
        # Build a human-readable summary of what changed
        change_parts = []
        for c in upd.get("changes", []):
            flag = " (large change ⚠️)" if c.get("large_change") else ""
            change_parts.append(f"{c['field']}: {c['old']} → {c['new']}{flag}")
        update_info = " | ".join(change_parts) if change_parts else "Fields updated"

        po = upd.get("current", {})
        row_idx = _find_row(existing_data, upd["po_number"])

        if row_idx:
            # Update existing row in place
            _update_row(
                service, sheet_id, tab_name, row_idx,
                _build_row(
                    po_number=upd["po_number"],
                    status="Updated",
                    update_info=update_info,
                    category=po.get("category", ""),
                )
            )
            _colour_row(service, sheet_id, tab_name, row_idx, COLOR_UPDATED)
        else:
            # PO not yet in sheet — add it
            row = _build_row(
                po_number=upd["po_number"],
                status="Updated",
                update_info=update_info,
                category=po.get("category", ""),
            )
            _append_row(service, sheet_id, tab_name, row)
            _colour_last_row(service, sheet_id, tab_name, COLOR_UPDATED, existing_data)

        summary["updated"] += 1

    # ── Cancelled / Removed POs ──
    for po in changes.get("cancelled", []):
        row_idx = _find_row(existing_data, po.get("po_number", ""))

        if row_idx:
            _update_row(
                service, sheet_id, tab_name, row_idx,
                _build_row(
                    po_number=po.get("po_number", ""),
                    status="Removed",
                    update_info="PO cancelled / removed from portal",
                    category=po.get("category", ""),
                )
            )
            _colour_row(service, sheet_id, tab_name, row_idx, COLOR_CANCELLED)
        else:
            row = _build_row(
                po_number=po.get("po_number", ""),
                status="Removed",
                update_info="PO cancelled / removed from portal",
                category=po.get("category", ""),
            )
            _append_row(service, sheet_id, tab_name, row)
            _colour_last_row(service, sheet_id, tab_name, COLOR_CANCELLED, existing_data)

        summary["cancelled"] += 1

    log.info(
        f"✅ Sheet sync done — "
        f"New Issued: {summary['new']} | "
        f"Updated: {summary['updated']} | "
        f"Removed: {summary['cancelled']}"
    )


# ─────────────────────────────────────────────────────────────
#  ROW BUILDERS
# ─────────────────────────────────────────────────────────────

def _build_row(po_number: str, status: str, update_info: str, category: str) -> list:
    """Build a 5-column row: Date | PO Number | Status | Update Info | Category"""
    return [
        datetime.now().strftime("%d-%m-%Y"),   # A: Date
        po_number,                              # B: PO Number
        status,                                 # C: Status (New Issued / Updated / Removed)
        update_info,                            # D: Update Info
        _normalise_category(category),          # E: Category
    ]


def _normalise_category(raw: str) -> str:
    """Map portal category text to Girls / Women / Unknown."""
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

def _append_row(service, spreadsheet_id: str, tab_name: str, row: list):
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A:E",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def _update_row(service, spreadsheet_id: str, tab_name: str, row_idx: int, row: list):
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A{row_idx}:E{row_idx}",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()


def _colour_row(service, spreadsheet_id: str, tab_name: str, row_idx: int, color: dict):
    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    if sheet_id is not None:
        _highlight_row(service, spreadsheet_id, sheet_id, row_idx, color)


def _colour_last_row(service, spreadsheet_id: str, tab_name: str, color: dict, existing_data: list):
    """Colour the last row that was just appended."""
    data = _load_sheet_data(service, spreadsheet_id, tab_name)
    last_idx = len(data)  # 1-based, header is row 1
    if last_idx > 1:
        _colour_row(service, spreadsheet_id, tab_name, last_idx, color)


def _find_row(sheet_data: list, po_number: str) -> int | None:
    """Find 1-based row index by PO number (column B = index 1)."""
    if not po_number or not sheet_data:
        return None
    for idx, row in enumerate(sheet_data):
        if idx == 0:
            continue  # skip header
        if len(row) > 1 and str(row[1]).strip() == str(po_number).strip():
            return idx + 1
    return None


def _load_sheet_data(service, spreadsheet_id: str, tab_name: str) -> list:
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{tab_name}!A:E",
        ).execute()
        return result.get("values", [])
    except Exception as e:
        log.debug(f"Could not load sheet: {e}")
        return []


def _highlight_row(service, spreadsheet_id: str, sheet_id: int, row_idx: int, color: dict):
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
                "cell": {"userEnteredFormat": {"backgroundColor": color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }]},
    ).execute()


def _format_header_row(service, spreadsheet_id: str, sheet_id: int):
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": COLOR_HEADER,
                            "textFormat": {
                                "bold": True,
                                "foregroundColor": COLOR_WHITE,
                            },
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
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


def _get_sheet_id(service, spreadsheet_id: str, tab_name: str) -> int | None:
    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in meta["sheets"]:
            if sheet["properties"]["title"] == tab_name:
                return sheet["properties"]["sheetId"]
    except Exception as e:
        log.debug(f"Could not get sheet ID: {e}")
    return None


def _log_changes_locally(changes: dict, config: dict):
    import json
    from pathlib import Path
    Path("logs").mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(f"logs/{config['customer_id']}_changes_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(changes, f, indent=2, ensure_ascii=False, default=str)
    log.info(f"📁 Changes saved locally → {path}")

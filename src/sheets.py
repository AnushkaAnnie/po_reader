"""
sheets.py — Write PO changes to Google Sheets
Handles: new rows, status updates, field updates, cancellation highlighting
"""

import os
import re
from datetime import datetime
from src.logger import get_logger

log = get_logger()

# ── Colours for row highlighting (RGB as integers for Sheets API) ──
COLOR_NEW        = {"red": 0.85, "green": 0.93, "blue": 0.83}   # Light green
COLOR_CANCELLED  = {"red": 0.96, "green": 0.80, "blue": 0.80}   # Light red
COLOR_UPDATED    = {"red": 1.00, "green": 0.95, "blue": 0.80}   # Light amber
COLOR_HEADER     = {"red": 0.20, "green": 0.20, "blue": 0.20}   # Dark grey
COLOR_WHITE      = {"red": 1.00, "green": 1.00, "blue": 1.00}


def get_sheets_service():
    """Build and return authenticated Google Sheets API service."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./service-account.json")
    if not os.path.exists(sa_file):
        raise FileNotFoundError(
            f"Google service account file not found: {sa_file}\n"
            f"Please follow the setup guide in README.md to create one."
        )

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds  = Credentials.from_service_account_file(sa_file, scopes=scopes)
    return build("sheets", "v4", credentials=creds)


def ensure_sheet_headers(service, spreadsheet_id: str, tab_name: str, col_map: dict):
    """
    Create the sheet tab if it doesn't exist and write column headers.
    Safe to call on every run — only writes if headers are missing.
    """
    # Get existing sheets
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_titles = [s["properties"]["title"] for s in meta["sheets"]]

    if tab_name not in sheet_titles:
        # Create the tab
        body = {
            "requests": [{
                "addSheet": {"properties": {"title": tab_name}}
            }]
        }
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body
        ).execute()
        log.info(f"📄 Created sheet tab: {tab_name}")

    # Check if header row already exists
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1:Z1",
    ).execute()
    existing = result.get("values", [[]])[0]

    # Build header row from column map (sort by column letter)
    headers = _build_header_row(col_map)

    if existing == headers:
        log.debug("Sheet headers already correct")
        return

    # Write headers
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()

    # Format header row
    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    _format_header_row(service, spreadsheet_id, sheet_id)
    log.info(f"✅ Sheet headers written to '{tab_name}'")


def write_changes_to_sheet(changes: dict, config: dict):
    """
    Main entry point — write all PO changes to the customer's Google Sheet.
    """
    sheet_id  = config["sheet_id"]
    tab_name  = config["google_sheet"]["tab_name"]
    col_map   = config["google_sheet"]["columns"]
    customer  = config["customer_name"]

    if not sheet_id or sheet_id == "YOUR_GOOGLE_SHEET_ID_HERE":
        log.warning(
            f"⚠️  Google Sheet ID not configured for {customer}. "
            f"Set {config['google_sheet']['sheet_id_env']} in your .env file."
        )
        _log_changes_locally(changes, config)
        return

    try:
        service = get_sheets_service()
    except FileNotFoundError as e:
        log.warning(f"⚠️  {e}")
        log.info("Writing changes to local log instead...")
        _log_changes_locally(changes, config)
        return

    ensure_sheet_headers(service, sheet_id, tab_name, col_map)

    # Load existing data for lookups
    existing_data = _load_sheet_data(service, sheet_id, tab_name)

    summary = {"new": 0, "updated": 0, "cancelled": 0}

    # ── Write new POs ──
    if changes["new"]:
        for po in changes["new"]:
            _append_po_row(service, sheet_id, tab_name, col_map, po, COLOR_NEW)
            summary["new"] += 1
        log.info(f"📝 Wrote {summary['new']} new PO(s) to sheet")

    # ── Update changed POs ──
    if changes["updated"]:
        for update in changes["updated"]:
            row_idx = _find_row_by_po_number(
                existing_data, update["po_number"], col_map
            )
            if row_idx:
                _update_po_row(
                    service, sheet_id, tab_name, col_map,
                    update["current"], row_idx, COLOR_UPDATED
                )
                summary["updated"] += 1
            else:
                # PO not in sheet yet — add it
                _append_po_row(
                    service, sheet_id, tab_name, col_map,
                    update["current"], COLOR_UPDATED
                )
                summary["updated"] += 1
        log.info(f"✏️  Updated {summary['updated']} PO(s) in sheet")

    # ── Mark cancelled POs ──
    if changes["cancelled"]:
        for po in changes["cancelled"]:
            row_idx = _find_row_by_po_number(
                existing_data, po.get("po_number", ""), col_map
            )
            if row_idx:
                _mark_cancelled(
                    service, sheet_id, tab_name, col_map, po, row_idx
                )
                summary["cancelled"] += 1
            else:
                po["status"] = "CANCELLED"
                _append_po_row(
                    service, sheet_id, tab_name, col_map, po, COLOR_CANCELLED
                )
                summary["cancelled"] += 1
        log.info(f"❌ Marked {summary['cancelled']} PO(s) as cancelled")

    log.info(
        f"✅ Sheet sync complete — "
        f"New: {summary['new']} | Updated: {summary['updated']} | Cancelled: {summary['cancelled']}"
    )


# ─────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _build_header_row(col_map: dict) -> list[str]:
    """Convert column map (field → letter) to ordered header list."""
    cols = sorted(col_map.items(), key=lambda x: x[1])
    label_map = {
        "po_number":    "PO Number",
        "vendor_code":  "Vendor Code",
        "vendor_name":  "Vendor Name",
        "vendor":       "Vendor",
        "po_date":      "PO Date",
        "delivery_date":"Delivery Date",
        "amount":       "Amount",
        "currency":     "Currency",
        "status":       "Status",
        "line_items":   "Line Items",
        "remarks":      "Remarks",
        "last_synced":  "Last Synced",
    }
    return [label_map.get(field, field.replace("_", " ").title()) for field, _ in cols]


def _po_to_row(po: dict, col_map: dict) -> list[str]:
    """Convert a PO dict to an ordered list matching col_map columns."""
    sorted_fields = sorted(col_map.items(), key=lambda x: x[1])
    row = []
    for field, _ in sorted_fields:
        if field == "last_synced":
            row.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        elif field == "vendor_name" and not po.get("vendor_name"):
            row.append(po.get("vendor", ""))
        else:
            row.append(str(po.get(field, "")).strip())
    return row


def _col_letter_to_index(letter: str) -> int:
    """Convert column letter (A=0, B=1, ...) to zero-based index."""
    letter = letter.upper().strip()
    result = 0
    for char in letter:
        result = result * 26 + (ord(char) - ord('A') + 1)
    return result - 1


def _load_sheet_data(service, spreadsheet_id: str, tab_name: str) -> list[list]:
    """Load all current sheet data."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{tab_name}!A:Z",
        ).execute()
        return result.get("values", [])
    except Exception as e:
        log.debug(f"Could not load sheet data: {e}")
        return []


def _find_row_by_po_number(
    sheet_data: list[list], po_number: str, col_map: dict
) -> int | None:
    """
    Find the 1-based row index of a PO in the sheet.
    Returns None if not found.
    """
    if not po_number or not sheet_data:
        return None

    po_col_idx = _col_letter_to_index(col_map.get("po_number", "A"))

    for idx, row in enumerate(sheet_data):
        if idx == 0:
            continue  # Skip header
        if po_col_idx < len(row) and str(row[po_col_idx]).strip() == str(po_number).strip():
            return idx + 1  # 1-based row number

    return None


def _append_po_row(service, spreadsheet_id, tab_name, col_map, po, color):
    """Append a new row to the sheet."""
    row = _po_to_row(po, col_map)
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A:A",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    # Colour the row
    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    if sheet_id is not None:
        # Re-load to find the row we just added
        data = _load_sheet_data(service, spreadsheet_id, tab_name)
        row_idx = _find_row_by_po_number(data, po.get("po_number", ""), col_map)
        if row_idx:
            _highlight_row(service, spreadsheet_id, sheet_id, row_idx, color, len(col_map))


def _update_po_row(service, spreadsheet_id, tab_name, col_map, po, row_idx, color):
    """Update all cells in an existing row."""
    row = _po_to_row(po, col_map)
    num_cols = len(col_map)
    end_col  = chr(ord('A') + num_cols - 1)
    range_   = f"{tab_name}!A{row_idx}:{end_col}{row_idx}"

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_,
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()

    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    if sheet_id is not None:
        _highlight_row(service, spreadsheet_id, sheet_id, row_idx, color, num_cols)


def _mark_cancelled(service, spreadsheet_id, tab_name, col_map, po, row_idx):
    """Update status to CANCELLED and highlight row red."""
    status_col = col_map.get("status", "H")
    sync_col   = col_map.get("last_synced", "K")

    updates = [
        {
            "range": f"{tab_name}!{status_col}{row_idx}",
            "values": [["CANCELLED"]],
        },
        {
            "range": f"{tab_name}!{sync_col}{row_idx}",
            "values": [[datetime.now().strftime("%Y-%m-%d %H:%M:%S")]],
        },
    ]
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": updates},
    ).execute()

    sheet_id = _get_sheet_id(service, spreadsheet_id, tab_name)
    if sheet_id is not None:
        _highlight_row(
            service, spreadsheet_id, sheet_id,
            row_idx, COLOR_CANCELLED, len(col_map)
        )


def _highlight_row(service, spreadsheet_id, sheet_id, row_idx, color, num_cols):
    """Set background colour for an entire row."""
    request = {
        "repeatCell": {
            "range": {
                "sheetId":          sheet_id,
                "startRowIndex":    row_idx - 1,
                "endRowIndex":      row_idx,
                "startColumnIndex": 0,
                "endColumnIndex":   num_cols,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": color
                }
            },
            "fields": "userEnteredFormat.backgroundColor",
        }
    }
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [request]},
    ).execute()


def _format_header_row(service, spreadsheet_id, sheet_id):
    """Bold + dark background for header row."""
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                },
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
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def _get_sheet_id(service, spreadsheet_id: str, tab_name: str) -> int | None:
    """Return the numeric sheetId for a named tab."""
    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in meta["sheets"]:
            if sheet["properties"]["title"] == tab_name:
                return sheet["properties"]["sheetId"]
    except Exception as e:
        log.debug(f"Could not get sheet ID: {e}")
    return None


def _log_changes_locally(changes: dict, config: dict):
    """Fallback: write changes to a local JSON file when Sheets is unavailable."""
    import json
    from pathlib import Path

    Path("logs").mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(f"logs/{config['customer_id']}_changes_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(changes, f, indent=2, ensure_ascii=False, default=str)
    log.info(f"📁 Changes saved locally → {path}")

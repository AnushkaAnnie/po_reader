"""
reset_sheet.py — Run this ONCE to clean up the existing messy sheet.
Clears all old data, old columns, and resets with clean 5-column headers.

Usage:
    python reset_sheet.py
"""

import os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from src.sheets import get_sheets_service, clear_and_reset_sheet
from src.logger import get_logger

log = get_logger()

SHEET_ID = os.getenv("V2RETAIL_SHEET_ID", "")
TAB_NAME = "PO Tracker"

if not SHEET_ID:
    print("ERROR: V2RETAIL_SHEET_ID not set in .env file")
    sys.exit(1)

print(f"\n🧹 Resetting sheet: {TAB_NAME}")
print(f"   Sheet ID: {SHEET_ID}")
print(f"   This will DELETE all existing data and start fresh.")

confirm = input("\nType 'yes' to confirm: ").strip().lower()
if confirm != "yes":
    print("Cancelled.")
    sys.exit(0)

service = get_sheets_service()
clear_and_reset_sheet(service, SHEET_ID, TAB_NAME)

print("\n✅ Sheet reset complete!")
print(f"   New columns: Date | PO Number | Status | Update Info | Category")
print(f"\nNow run: python main.py --customer v2retail")

"""
main.py — Master orchestrator
Usage:
    python main.py                          # run all customers
    python main.py --customer v2retail      # run one customer
    python main.py --test-login             # test login only
    python main.py --dry-run                # crawl + diff, no sheet writes
"""

import os
import sys
import argparse
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from src.logger import get_logger
from src.config import load_all_customers, load_customer_config
from src.crawler import start_browser, login, extract_po_list, extract_po_detail
from src.diff import detect_changes, save_snapshot
from src.validator import validate_po_list
from src.sheets import write_changes_to_sheet
from src.notifications import send_alerts

log = get_logger()


def run_pipeline(config: dict, dry_run: bool = False, test_login: bool = False):
    customer    = config["customer_name"]
    customer_id = config["customer_id"]
    start_time  = datetime.now()
    session     = None

    log.info(f"\n{'═'*60}")
    log.info(f"  🚀  Starting pipeline: {customer}")
    log.info(f"{'═'*60}")

    try:
        # Step 1 — Auth
        session, _, _ = start_browser()
        login(session, config)

        if test_login:
            log.info("✅ Login test passed")
            return {"status": "login_ok", "customer": customer}

        # Step 2 — Crawl
        po_list = extract_po_list(session, config)
        if not po_list:
            log.warning("⚠️  No POs found — check logs/ for screenshot")
            return {"status": "no_pos", "customer": customer}

        # Step 3 — Enrich details
        log.info(f"🔎 Enriching {len(po_list)} PO(s)...")
        enriched = [extract_po_detail(session, po, config) for po in po_list]

        # Step 4 — Validate
        valid_pos, quarantined = validate_po_list(enriched, config)
        if not valid_pos:
            log.warning("⚠️  All POs failed validation")
            return {"status": "all_quarantined", "customer": customer}

        # Step 5 — Diff
        changes = detect_changes(valid_pos, customer_id, config)
        has_changes = any([changes["new"], changes["cancelled"], changes["updated"]])

        if not has_changes:
            log.info("✅ No changes — sheet unchanged")
            save_snapshot(valid_pos, customer_id)
            return {"status": "no_changes", "customer": customer}

        if dry_run:
            log.info("🧪 DRY RUN — changes found but NOT written to sheet")
            _print_changes(changes)
            save_snapshot(valid_pos, customer_id)
            return {"status": "dry_run", "customer": customer, "changes": changes}

        # Step 6 — Write sheet
        write_changes_to_sheet(changes, config)

        # Step 7 — Save snapshot
        save_snapshot(valid_pos, customer_id)

        # Step 8 — Notify
        send_alerts(changes, config)

        elapsed = (datetime.now() - start_time).seconds
        log.info(
            f"\n✅ Done — {customer} ({elapsed}s) | "
            f"New: {len(changes['new'])} | "
            f"Updated: {len(changes['updated'])} | "
            f"Cancelled: {len(changes['cancelled'])}"
        )
        return {"status": "success", "customer": customer, "changes": changes}

    except Exception as e:
        log.error(f"❌ Pipeline failed: {e}")
        log.debug(traceback.format_exc())
        return {"status": "error", "customer": customer, "error": str(e)}

    finally:
        if session:
            session.stop()


def run_all(dry_run: bool = False, test_login: bool = False):
    configs = load_all_customers()
    if not configs:
        log.error("No customers found in customers/")
        sys.exit(1)

    log.info(f"📦 Running for {len(configs)} customer(s)")
    results = [run_pipeline(c, dry_run=dry_run, test_login=test_login) for c in configs]

    log.info(f"\n{'═'*60}\n  📊  SUMMARY\n{'═'*60}")
    icons = {"success": "✅", "no_changes": "➖", "dry_run": "🧪",
             "login_ok": "🔐", "no_pos": "⚠️", "all_quarantined": "🚫", "error": "❌"}
    for r in results:
        log.info(f"  {icons.get(r['status'],'❓')}  {r['customer']} — {r['status']}")
    return results


def _print_changes(changes: dict):
    if changes["new"]:
        log.info(f"🆕 New POs ({len(changes['new'])}):")
        for po in changes["new"]:
            log.info(f"   • {po.get('po_number')} | {po.get('vendor')} | {po.get('amount')}")
    if changes["cancelled"]:
        log.info(f"❌ Cancelled ({len(changes['cancelled'])}):")
        for po in changes["cancelled"]:
            log.info(f"   • {po.get('po_number')}")
    if changes["updated"]:
        log.info(f"✏️  Updated ({len(changes['updated'])}):")
        for u in changes["updated"]:
            log.info(f"   • {u['po_number']}: {len(u['changes'])} field(s) changed")


def main():
    parser = argparse.ArgumentParser(description="SRM Portal PO Automation")
    parser.add_argument("--customer", help="Customer ID (e.g. v2retail)")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--test-login", action="store_true")
    args = parser.parse_args()

    if args.customer:
        yaml_path = f"customers/{args.customer}.yaml"
        if not Path(yaml_path).exists():
            log.error(f"Not found: {yaml_path}")
            sys.exit(1)
        config = load_customer_config(yaml_path)
        run_pipeline(config, dry_run=args.dry_run, test_login=args.test_login)
    else:
        run_all(dry_run=args.dry_run, test_login=args.test_login)


if __name__ == "__main__":
    main()

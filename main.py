"""
main.py — Master orchestrator
Runs the full SRM automation pipeline for every configured customer
Usage:
    python main.py                     # run all customers
    python main.py --customer v2retail # run one customer
    python main.py --test-login        # only test login, no sheet writes
    python main.py --dry-run           # full crawl + diff but no writes
"""

import os
import sys
import argparse
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Add project root to path so `src.*` imports work
sys.path.insert(0, str(Path(__file__).parent))

from src.logger import get_logger
from src.config import load_all_customers, load_customer_config
from src.crawler import start_browser, login, extract_po_list, extract_po_detail
from src.diff import detect_changes, save_snapshot
from src.validator import validate_po_list
from src.sheets import write_changes_to_sheet
from src.notifications import send_alerts

log = get_logger()

HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"


# ─────────────────────────────────────────────────────────────
#  PIPELINE — single customer
# ─────────────────────────────────────────────────────────────

def run_pipeline(config: dict, dry_run: bool = False, test_login: bool = False):
    """
    Full automation pipeline for one customer.
    Steps: Auth → Crawl → Validate → Diff → Write Sheet → Notify
    """
    customer   = config["customer_name"]
    customer_id = config["customer_id"]

    log.info(f"\n{'═' * 60}")
    log.info(f"  🚀  Starting pipeline: {customer}")
    log.info(f"{'═' * 60}")

    start_time = datetime.now()
    pw = browser = context = page = None

    try:
        # ── Step 1: Auth ──────────────────────────────────────
        pw, browser, context = start_browser(headless=HEADLESS)
        page = context.new_page()

        login(page, config)

        if test_login:
            log.info("✅ Login test passed — stopping here (--test-login mode)")
            return {"status": "login_ok", "customer": customer}

        # ── Step 2: Crawl PO list ──────────────────────────────
        po_list = extract_po_list(page, config)

        if not po_list:
            log.warning(f"⚠️  No POs found for {customer} — check crawler selectors")
            return {"status": "no_pos", "customer": customer}

        # ── Step 3: Enrich with detail pages (for new/changed) ──
        log.info(f"🔎 Enriching PO details ({len(po_list)} POs)...")
        enriched = []
        for po in po_list:
            enriched.append(extract_po_detail(page, po, config))

        # ── Step 4: Validate ──────────────────────────────────
        valid_pos, quarantined = validate_po_list(enriched, config)

        if not valid_pos:
            log.warning("⚠️  All POs failed validation — nothing to write")
            return {"status": "all_quarantined", "customer": customer}

        # ── Step 5: Diff ──────────────────────────────────────
        changes = detect_changes(valid_pos, customer_id, config)

        has_changes = (
            len(changes["new"]) +
            len(changes["cancelled"]) +
            len(changes["updated"])
        ) > 0

        if not has_changes:
            log.info(f"✅ No changes for {customer} — sheet unchanged")
            # Still save snapshot so next run has a baseline
            save_snapshot(valid_pos, customer_id)
            return {"status": "no_changes", "customer": customer}

        if dry_run:
            log.info("🧪 DRY RUN — changes detected but not written to sheet")
            _print_dry_run_summary(changes)
            save_snapshot(valid_pos, customer_id)
            return {"status": "dry_run", "customer": customer, "changes": changes}

        # ── Step 6: Write to Google Sheet ────────────────────
        write_changes_to_sheet(changes, config)

        # ── Step 7: Save snapshot ────────────────────────────
        save_snapshot(valid_pos, customer_id)

        # ── Step 8: Notify ───────────────────────────────────
        send_alerts(changes, config)

        elapsed = (datetime.now() - start_time).seconds
        log.info(
            f"\n✅ Pipeline complete for {customer} "
            f"({elapsed}s) — "
            f"New: {len(changes['new'])} | "
            f"Updated: {len(changes['updated'])} | "
            f"Cancelled: {len(changes['cancelled'])}"
        )

        return {
            "status":    "success",
            "customer":  customer,
            "changes":   changes,
            "elapsed_s": elapsed,
        }

    except Exception as e:
        log.error(f"❌ Pipeline failed for {customer}: {e}")
        log.debug(traceback.format_exc())
        return {"status": "error", "customer": customer, "error": str(e)}

    finally:
        # Always clean up browser
        try:
            if page:    page.close()
            if context: context.close()
            if browser: browser.close()
            if pw:      pw.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
#  MULTI-CUSTOMER RUNNER
# ─────────────────────────────────────────────────────────────

def run_all(dry_run: bool = False, test_login: bool = False):
    """Run the pipeline for every customer in customers/."""
    configs = load_all_customers()

    if not configs:
        log.error("No customers configured. Add a YAML file to customers/")
        sys.exit(1)

    log.info(f"📦 Running automation for {len(configs)} customer(s)")

    results = []
    for config in configs:
        result = run_pipeline(config, dry_run=dry_run, test_login=test_login)
        results.append(result)

    # ── Summary ──
    log.info(f"\n{'═' * 60}")
    log.info("  📊  RUN SUMMARY")
    log.info(f"{'═' * 60}")
    for r in results:
        status_icon = {
            "success":       "✅",
            "no_changes":    "➖",
            "dry_run":       "🧪",
            "login_ok":      "🔐",
            "no_pos":        "⚠️",
            "all_quarantined":"🚫",
            "error":         "❌",
        }.get(r["status"], "❓")
        log.info(f"  {status_icon}  {r['customer']} — {r['status']}")

    return results


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────

def _print_dry_run_summary(changes: dict):
    log.info("\n── DRY RUN CHANGES ──────────────────────────")
    if changes["new"]:
        log.info(f"🆕 Would add {len(changes['new'])} new PO(s):")
        for po in changes["new"]:
            log.info(
                f"   • {po.get('po_number')} | "
                f"{po.get('vendor') or po.get('vendor_name')} | "
                f"{po.get('amount')}"
            )
    if changes["cancelled"]:
        log.info(f"❌ Would mark {len(changes['cancelled'])} PO(s) as cancelled:")
        for po in changes["cancelled"]:
            log.info(f"   • {po.get('po_number')}")
    if changes["updated"]:
        log.info(f"✏️  Would update {len(changes['updated'])} PO(s):")
        for upd in changes["updated"]:
            log.info(f"   • {upd['po_number']}: {len(upd['changes'])} field(s) changed")
    log.info("─────────────────────────────────────────────")


# ─────────────────────────────────────────────────────────────
#  CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SRM Portal PO Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          Run all customers
  python main.py --customer v2retail      Run only V2 Retail
  python main.py --test-login             Test login only (no sheet writes)
  python main.py --dry-run                Crawl + diff but don't write
  python main.py --customer v2retail --dry-run
        """
    )
    parser.add_argument(
        "--customer",
        help="Customer ID to run (e.g. v2retail). Omit to run all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Crawl and detect changes but do NOT write to Google Sheet",
    )
    parser.add_argument(
        "--test-login",
        action="store_true",
        help="Only test portal login and exit",
    )

    args = parser.parse_args()

    if args.customer:
        yaml_path = f"customers/{args.customer}.yaml"
        if not Path(yaml_path).exists():
            log.error(f"Customer file not found: {yaml_path}")
            sys.exit(1)
        config = load_customer_config(yaml_path)
        run_pipeline(config, dry_run=args.dry_run, test_login=args.test_login)
    else:
        run_all(dry_run=args.dry_run, test_login=args.test_login)


if __name__ == "__main__":
    main()

"""
scheduler.py — Keep the automation running on a schedule
Respects per-customer intervals and active hours from their YAML config
Usage:
    python scheduler.py
"""

import time
import random
import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from src.logger import get_logger
from src.config import load_all_customers
from main import run_pipeline

log = get_logger()


def is_within_active_hours(config: dict) -> bool:
    """Check if current time is within the customer's configured active hours."""
    schedule  = config.get("schedule", {})
    tz_str    = schedule.get("timezone", "Asia/Kolkata")
    start_str = schedule.get("active_hours_start", "00:00")
    end_str   = schedule.get("active_hours_end",   "23:59")

    try:
        tz   = ZoneInfo(tz_str)
        now  = datetime.now(tz).time()
        start = dtime(*map(int, start_str.split(":")))
        end   = dtime(*map(int, end_str.split(":")))
        return start <= now <= end
    except Exception as e:
        log.debug(f"Active hours check failed: {e}")
        return True  # Default to always active on error


def get_next_run_seconds(config: dict) -> float:
    """Return seconds until next run, including jitter."""
    schedule        = config.get("schedule", {})
    interval_min    = int(schedule.get("interval_minutes", 120))
    jitter_sec      = int(schedule.get("jitter_seconds", 90))
    jitter          = random.uniform(-jitter_sec, jitter_sec)
    return max(60, interval_min * 60 + jitter)


class CustomerSchedule:
    """Tracks the last run time for a single customer."""
    def __init__(self, config: dict):
        self.config       = config
        self.customer_id  = config["customer_id"]
        self.name         = config["customer_name"]
        self.last_run     = None
        self.next_run_in  = 0.0          # seconds

    def is_due(self) -> bool:
        if self.last_run is None:
            return True
        elapsed = (datetime.now() - self.last_run).total_seconds()
        return elapsed >= self.next_run_in

    def mark_ran(self):
        self.last_run    = datetime.now()
        self.next_run_in = get_next_run_seconds(self.config)
        next_at = datetime.now().timestamp() + self.next_run_in
        next_dt = datetime.fromtimestamp(next_at).strftime("%H:%M:%S")
        log.info(
            f"⏰  Next run for {self.name} in "
            f"{self.next_run_in/60:.1f} min (≈ {next_dt})"
        )


def run_scheduler():
    log.info("🕐 SRM Scheduler started")
    log.info("   Press Ctrl+C to stop\n")

    # Load all customers and create schedule trackers
    configs   = load_all_customers()
    schedules = [CustomerSchedule(cfg) for cfg in configs]

    if not schedules:
        log.error("No customers found. Exiting.")
        return

    log.info(f"Monitoring {len(schedules)} customer(s):")
    for s in schedules:
        cfg = s.config.get("schedule", {})
        log.info(
            f"  • {s.name} — every {cfg.get('interval_minutes', 120)} min "
            f"({cfg.get('active_hours_start','00:00')}–{cfg.get('active_hours_end','23:59')} "
            f"{cfg.get('timezone','Asia/Kolkata')})"
        )

    try:
        while True:
            ran_any = False

            for sched in schedules:
                if not sched.is_due():
                    continue

                if not is_within_active_hours(sched.config):
                    log.debug(f"Outside active hours for {sched.name} — skipping")
                    sched.mark_ran()  # Reset timer so we check again next cycle
                    continue

                log.info(f"\n▶  Running pipeline for {sched.name}...")
                run_pipeline(sched.config)
                sched.mark_ran()
                ran_any = True

            if not ran_any:
                # Find how long until the soonest customer is due
                soonest = min(
                    max(0, s.next_run_in - (datetime.now() - s.last_run).total_seconds())
                    if s.last_run else 0
                    for s in schedules
                )
                sleep_sec = min(max(soonest, 30), 300)  # Check at least every 5 min
                log.debug(f"All customers up to date — sleeping {sleep_sec:.0f}s")
                time.sleep(sleep_sec)

    except KeyboardInterrupt:
        log.info("\n👋 Scheduler stopped by user")


if __name__ == "__main__":
    run_scheduler()

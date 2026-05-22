"""
crawler.py — Login to SRM portal and extract all PO data
Handles V2 Retail's React SPA (srm.v2retail.com)
"""

import os
import time
from playwright.sync_api import sync_playwright, Page, Browser, Playwright
from src.logger import get_logger

log = get_logger()

# ── How long to wait (ms) for the SPA to render ──
WAIT_AFTER_LOGIN   = 4000
WAIT_AFTER_NAV     = 3000
WAIT_SCROLL        = 1500


# ─────────────────────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────────────────────

def start_browser(headless: bool = True):
    """Launch Playwright browser. Returns (playwright, browser, context)."""
    import shutil
    pw = sync_playwright().start()

    # Find Chrome/Chromium executable
    chrome_candidates = [
        "/opt/google/chrome/chrome",
        "/opt/chromium/chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
    ]
    exe = next((p for p in chrome_candidates if os.path.exists(p)), None)

    launch_kwargs = dict(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    if exe:
        launch_kwargs["executable_path"] = exe
        log.debug(f"Using Chrome at: {exe}")

    browser = pw.chromium.launch(**launch_kwargs)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    return pw, browser, context


def login(page: Page, config: dict) -> bool:
    """
    Navigate to the portal and log in.
    Returns True on success, raises on failure.
    """
    url      = config["portal_url"]
    username = config["username"]
    password = config["password"]
    customer = config["customer_name"]

    log.info(f"🔐 Logging in to {customer} portal → {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(WAIT_AFTER_LOGIN)

    # ── Try common login field selectors ──
    # V2 Retail is a React SPA — fields may use placeholder text
    user_selectors = [
        'input[type="text"]',
        'input[placeholder*="username" i]',
        'input[placeholder*="user" i]',
        'input[placeholder*="vendor" i]',
        'input[placeholder*="login" i]',
        'input[name="username"]',
        'input[id*="user" i]',
    ]
    pass_selectors = [
        'input[type="password"]',
        'input[placeholder*="password" i]',
        'input[placeholder*="pass" i]',
        'input[name="password"]',
    ]
    submit_selectors = [
        'button[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'button:has-text("Log In")',
        'input[type="submit"]',
    ]

    # Fill username
    user_filled = _try_fill(page, user_selectors, username, "username")
    if not user_filled:
        _screenshot(page, customer, "login_fail_no_username")
        raise RuntimeError(
            "Could not find username field. "
            "Run with HEADLESS=false to inspect the portal manually."
        )

    # Fill password
    pass_filled = _try_fill(page, pass_selectors, password, "password")
    if not pass_filled:
        _screenshot(page, customer, "login_fail_no_password")
        raise RuntimeError("Could not find password field.")

    page.wait_for_timeout(500)

    # Submit
    submitted = False
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                submitted = True
                log.debug(f"Clicked submit: {sel}")
                break
        except Exception:
            continue

    if not submitted:
        # Try pressing Enter as fallback
        page.keyboard.press("Enter")
        log.warning("Submit button not found — pressed Enter instead")

    page.wait_for_timeout(WAIT_AFTER_LOGIN)

    # ── Verify login succeeded ──
    current_url = page.url
    page_text   = page.inner_text("body")

    fail_signals = ["invalid", "incorrect", "wrong", "error", "failed"]
    if any(s in page_text.lower() for s in fail_signals):
        _screenshot(page, customer, "login_fail_error_text")
        raise RuntimeError(
            f"Login failed — portal shows error message. "
            f"Check credentials for {customer}."
        )

    log.info(f"✅ Logged in successfully → {current_url}")
    return True


# ─────────────────────────────────────────────────────────────
#  PO LIST EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_po_list(page: Page, config: dict) -> list[dict]:
    """
    Navigate to the PO section and extract all POs.
    Returns a list of PO dicts.
    """
    customer   = config["customer_name"]
    portal_url = config["portal_url"]

    log.info(f"📋 Extracting PO list for {customer}...")

    # Try navigating to PO section
    po_paths = [
        "/purchase-orders",
        "/po",
        "/orders",
        "/vendor/orders",
        "/dashboard/po",
    ]

    navigated = False
    for path in po_paths:
        try:
            page.goto(portal_url + path, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(WAIT_AFTER_NAV)
            # Check we didn't get redirected back to login
            if "login" not in page.url.lower():
                navigated = True
                log.debug(f"PO page found at {path}")
                break
        except Exception:
            continue

    if not navigated:
        # Try clicking nav menu items
        log.info("Direct URL failed — trying nav menu...")
        nav_selectors = [
            'a:has-text("Purchase Order")',
            'a:has-text("PO")',
            'a:has-text("Orders")',
            '[href*="purchase"]',
            '[href*="order"]',
            'nav a',
        ]
        for sel in nav_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    page.wait_for_timeout(WAIT_AFTER_NAV)
                    log.debug(f"Clicked nav: {sel}")
                    break
            except Exception:
                continue

    # ── Scroll to load all data (SPA / infinite scroll) ──
    _scroll_to_bottom(page)

    # ── Extract PO rows ──
    pos = _extract_po_rows(page, config)

    if not pos:
        _screenshot(page, customer, "po_list_empty")
        log.warning(
            f"⚠️  No POs extracted for {customer}. "
            f"The portal selectors may need adjustment — "
            f"check the screenshot in logs/"
        )
    else:
        log.info(f"✅ Extracted {len(pos)} POs for {customer}")

    return pos


def _extract_po_rows(page: Page, config: dict) -> list[dict]:
    """Try multiple strategies to extract PO data from the current page."""

    # Strategy 1: Standard HTML table
    pos = _try_table_extraction(page)
    if pos:
        log.debug(f"Table strategy yielded {len(pos)} rows")
        return pos

    # Strategy 2: Card/list layout (common in React portals)
    pos = _try_card_extraction(page)
    if pos:
        log.debug(f"Card strategy yielded {len(pos)} rows")
        return pos

    # Strategy 3: Generic text extraction
    pos = _try_generic_extraction(page)
    if pos:
        log.debug(f"Generic strategy yielded {len(pos)} rows")
        return pos

    return []


def _try_table_extraction(page: Page) -> list[dict]:
    """Extract POs from an HTML table."""
    try:
        tables = page.locator("table").all()
        if not tables:
            return []

        for table in tables:
            rows = table.locator("tr").all()
            if len(rows) < 2:
                continue

            # Get headers
            header_cells = rows[0].locator("th, td").all()
            headers = [c.inner_text().strip().lower() for c in header_cells]

            if not any(h for h in headers if "po" in h or "order" in h or "purchase" in h):
                continue  # Not the PO table

            pos = []
            for row in rows[1:]:
                cells = row.locator("td").all()
                if not cells:
                    continue
                values = [c.inner_text().strip() for c in cells]
                po = _map_row_to_po(headers, values)
                if po.get("po_number"):
                    pos.append(po)
            return pos
    except Exception as e:
        log.debug(f"Table extraction failed: {e}")
    return []


def _try_card_extraction(page: Page) -> list[dict]:
    """Extract POs from card/list layout (React SPAs often use this)."""
    card_selectors = [
        "[class*='po-card']",
        "[class*='order-card']",
        "[class*='purchase-card']",
        "[class*='po-item']",
        "[class*='order-item']",
        "[class*='list-item']",
        "[data-testid*='po']",
        "[data-testid*='order']",
    ]
    try:
        for sel in card_selectors:
            cards = page.locator(sel).all()
            if not cards:
                continue

            pos = []
            for card in cards:
                text = card.inner_text()
                po = _parse_free_text_po(text)
                if po.get("po_number"):
                    pos.append(po)
            if pos:
                return pos
    except Exception as e:
        log.debug(f"Card extraction failed: {e}")
    return []


def _try_generic_extraction(page: Page) -> list[dict]:
    """
    Last resort: find all text on page that looks like PO numbers
    and extract surrounding context.
    """
    import re
    try:
        body_text = page.inner_text("body")
        # Common PO number patterns
        po_pattern = re.compile(
            r'(PO[-/]?\d{4,}|[A-Z]{2,4}-\d{4,}|\d{8,12})',
            re.IGNORECASE
        )
        matches = po_pattern.findall(body_text)
        if matches:
            # Deduplicate and return basic records
            seen = set()
            pos = []
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    pos.append({
                        "po_number": m,
                        "vendor": "",
                        "amount": "",
                        "status": "",
                        "po_date": "",
                        "delivery_date": "",
                        "currency": "",
                        "remarks": "",
                        "raw": True,   # Flag: needs manual field mapping
                    })
            return pos
    except Exception as e:
        log.debug(f"Generic extraction failed: {e}")
    return []


# ─────────────────────────────────────────────────────────────
#  PO DETAIL EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_po_detail(page: Page, po: dict, config: dict) -> dict:
    """
    Click into a PO and scrape its full detail page.
    Returns enriched PO dict.
    """
    if po.get("raw"):
        return po  # Can't navigate without a clickable element

    po_number = po.get("po_number", "")
    portal_url = config["portal_url"]

    try:
        # Try direct URL first
        detail_paths = [
            f"/purchase-orders/{po_number}",
            f"/po/{po_number}",
            f"/orders/{po_number}",
        ]
        for path in detail_paths:
            try:
                page.goto(portal_url + path, wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(WAIT_AFTER_NAV)
                if "login" not in page.url.lower():
                    break
            except Exception:
                continue

        # Extract detail fields
        detail_text = page.inner_text("body")
        enriched = _parse_free_text_po(detail_text)
        enriched["po_number"] = po_number  # Always preserve

        # Merge with existing data (detail wins)
        merged = {**po, **{k: v for k, v in enriched.items() if v}}
        return merged

    except Exception as e:
        log.debug(f"Detail extraction failed for {po_number}: {e}")
        return po   # Return original if detail fails


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────

def _map_row_to_po(headers: list[str], values: list[str]) -> dict:
    """Map table row to PO dict using header hints."""
    po = {
        "po_number": "", "vendor": "", "vendor_code": "",
        "amount": "", "currency": "", "status": "",
        "po_date": "", "delivery_date": "", "remarks": "",
    }
    for i, header in enumerate(headers):
        if i >= len(values):
            break
        val = values[i].strip()
        h = header.lower()
        if any(k in h for k in ["po no", "po num", "order no", "purchase no"]):
            po["po_number"] = val
        elif "vendor" in h and "code" in h:
            po["vendor_code"] = val
        elif "vendor" in h or "supplier" in h:
            po["vendor"] = val
        elif "amount" in h or "value" in h or "total" in h:
            po["amount"] = val
        elif "currency" in h or "curr" in h:
            po["currency"] = val
        elif "status" in h or "state" in h:
            po["status"] = val
        elif "delivery" in h or "due" in h:
            po["delivery_date"] = val
        elif "date" in h or "created" in h:
            po["po_date"] = val
        elif "remark" in h or "note" in h or "comment" in h:
            po["remarks"] = val
        elif not po["po_number"] and i == 0:
            po["po_number"] = val  # First column fallback
    return po


def _parse_free_text_po(text: str) -> dict:
    """
    Parse a PO from free-form text (detail page / card).
    Uses keyword proximity matching.
    """
    import re
    po = {
        "po_number": "", "vendor": "", "vendor_code": "",
        "amount": "", "currency": "", "status": "",
        "po_date": "", "delivery_date": "", "remarks": "",
    }

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for i, line in enumerate(lines):
        ll = line.lower()
        next_val = lines[i + 1] if i + 1 < len(lines) else ""

        if re.search(r'po\s*(no|num|number|#)', ll) and not po["po_number"]:
            po["po_number"] = _extract_value(line, next_val)

        elif re.search(r'vendor|supplier', ll) and "code" in ll and not po["vendor_code"]:
            po["vendor_code"] = _extract_value(line, next_val)

        elif re.search(r'vendor|supplier', ll) and not po["vendor"]:
            po["vendor"] = _extract_value(line, next_val)

        elif re.search(r'total|amount|value|net', ll) and not po["amount"]:
            po["amount"] = _extract_value(line, next_val)

        elif re.search(r'currency|curr\b', ll) and not po["currency"]:
            po["currency"] = _extract_value(line, next_val)

        elif re.search(r'status|state', ll) and not po["status"]:
            po["status"] = _extract_value(line, next_val)

        elif re.search(r'delivery|due\s*date|ship', ll) and not po["delivery_date"]:
            po["delivery_date"] = _extract_value(line, next_val)

        elif re.search(r'po\s*date|order\s*date|created', ll) and not po["po_date"]:
            po["po_date"] = _extract_value(line, next_val)

    # Fallback PO number: look for numeric string 6-12 digits
    if not po["po_number"]:
        match = re.search(r'\b\d{6,12}\b', text)
        if match:
            po["po_number"] = match.group()

    return po


def _extract_value(label_line: str, next_line: str) -> str:
    """
    Given 'PO Number: 12345', extract '12345'.
    Also handles label on one line, value on next.
    """
    import re
    # Try colon split on same line
    if ":" in label_line:
        parts = label_line.split(":", 1)
        val = parts[1].strip() if len(parts) > 1 else ""
        if val:
            return val
    # Value is on the next line
    if next_line and not any(
        kw in next_line.lower()
        for kw in ["po", "vendor", "amount", "status", "date", "delivery"]
    ):
        return next_line.strip()
    return ""


def _scroll_to_bottom(page: Page, max_scrolls: int = 10):
    """Scroll page to trigger lazy-loading in SPAs."""
    try:
        prev_height = 0
        for _ in range(max_scrolls):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(WAIT_SCROLL)
            curr_height = page.evaluate("document.body.scrollHeight")
            if curr_height == prev_height:
                break
            prev_height = curr_height
    except Exception:
        pass


def _try_fill(page: Page, selectors: list, value: str, field_name: str) -> bool:
    """Try filling a field using a list of selectors. Returns True on success."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.clear()
                el.fill(value)
                log.debug(f"Filled {field_name} with selector: {sel}")
                return True
        except Exception:
            continue
    return False


def _screenshot(page: Page, customer: str, label: str):
    """Save a debug screenshot to logs/."""
    from pathlib import Path
    from datetime import datetime
    Path("logs").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"logs/{customer}_{label}_{ts}.png"
    try:
        page.screenshot(path=fname, full_page=True)
        log.info(f"📸 Screenshot saved → {fname}")
    except Exception:
        pass

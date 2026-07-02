"""
crawler.py — Login to SRM portal and extract all PO data
Supports both Playwright (local) and Selenium (GitHub Actions CI)
Detected automatically via USE_SELENIUM env var
"""

import os
import re
from src.logger import get_logger

log = get_logger()

USE_SELENIUM = os.getenv("USE_SELENIUM", "false").lower() == "true"
CHROMIUM_PATH = os.getenv("CHROMIUM_PATH", "")
WAIT_MS = 4000


# ─────────────────────────────────────────────────────────────
#  BROWSER ABSTRACTION — same interface for Playwright & Selenium
# ─────────────────────────────────────────────────────────────

class BrowserSession:
    """Thin wrapper so the rest of the code doesn't care which driver is used."""

    def __init__(self):
        self._driver = None     # selenium
        self._page = None       # playwright
        self._pw = None
        self._browser = None
        self._context = None

    # ── Selenium ──────────────────────────────────────────────
    def _start_selenium(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--window-size=1280,900")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])

        if CHROMIUM_PATH and os.path.exists(CHROMIUM_PATH):
            opts.binary_location = CHROMIUM_PATH
            log.debug(f"Using Chromium at: {CHROMIUM_PATH}")

        # Auto-download matching ChromeDriver via webdriver-manager
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=opts)
        except Exception:
            # Fallback: rely on chromedriver already on PATH
            self._driver = webdriver.Chrome(options=opts)

        self._driver.implicitly_wait(5)
        log.info("✅ Selenium + Chrome browser started")

    # ── Playwright ────────────────────────────────────────────
    def _start_playwright(self):
        from playwright.sync_api import sync_playwright

        chrome_candidates = [
            "/opt/google/chrome/chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]
        exe = next((p for p in chrome_candidates if os.path.exists(p)), None)

        self._pw = sync_playwright().start()
        launch_kwargs = dict(
            headless=os.getenv("HEADLESS", "true").lower() != "false",
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

        self._browser = self._pw.chromium.launch(**launch_kwargs)
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()
        log.info("✅ Playwright browser started")

    def start(self):
        if USE_SELENIUM:
            self._start_selenium()
        else:
            self._start_playwright()
        return self

    def stop(self):
        try:
            if self._driver:
                self._driver.quit()
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ── Unified API ───────────────────────────────────────────
    def goto(self, url, timeout_ms=30000):
        if self._driver:
            self._driver.get(url)
        else:
            self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def wait(self, ms):
        import time
        time.sleep(ms / 1000)

    def find_and_fill(self, selectors: list, value: str, field: str) -> bool:
        """Try each selector until one works."""
        if self._driver:
            from selenium.webdriver.common.by import By
            for sel in selectors:
                try:
                    # Convert CSS selector
                    el = self._driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(value)
                        log.debug(f"Filled {field} via selenium: {sel}")
                        return True
                except Exception:
                    continue
            # Try by placeholder text
            for placeholder in [field, field.replace("_", " ")]:
                try:
                    el = self._driver.find_element(
                        By.XPATH,
                        f'//input[contains(@placeholder,"{placeholder}")]'
                    )
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(value)
                        return True
                except Exception:
                    continue
        else:
            for sel in selectors:
                try:
                    el = self._page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        el.clear()
                        el.fill(value)
                        log.debug(f"Filled {field} via playwright: {sel}")
                        return True
                except Exception:
                    continue
        return False

    def click(self, selectors: list) -> bool:
        if self._driver:
            from selenium.webdriver.common.by import By
            for sel in selectors:
                try:
                    el = self._driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        el.click()
                        return True
                except Exception:
                    continue
        else:
            for sel in selectors:
                try:
                    el = self._page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        el.click()
                        return True
                except Exception:
                    continue
        return False

    def get_text(self, selector: str) -> str:
        try:
            if self._driver:
                from selenium.webdriver.common.by import By
                return self._driver.find_element(By.CSS_SELECTOR, selector).text
            else:
                return self._page.locator(selector).inner_text()
        except Exception:
            return ""

    def page_text(self) -> str:
        try:
            if self._driver:
                from selenium.webdriver.common.by import By
                return self._driver.find_element(By.TAG_NAME, "body").text
            else:
                return self._page.inner_text("body")
        except Exception:
            return ""

    def page_source(self) -> str:
        try:
            if self._driver:
                return self._driver.page_source
            else:
                return self._page.content()
        except Exception:
            return ""

    def current_url(self) -> str:
        if self._driver:
            return self._driver.current_url
        else:
            return self._page.url

    def scroll_to_bottom(self, max_scrolls=10):
        import time
        try:
            prev_height = 0
            for _ in range(max_scrolls):
                if self._driver:
                    self._driver.execute_script(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                else:
                    self._page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                time.sleep(1.5)
                if self._driver:
                    curr = self._driver.execute_script(
                        "return document.body.scrollHeight"
                    )
                else:
                    curr = self._page.evaluate("document.body.scrollHeight")
                if curr == prev_height:
                    break
                prev_height = curr
        except Exception:
            pass

    def screenshot(self, path: str):
        try:
            if self._driver:
                self._driver.save_screenshot(path)
            else:
                self._page.screenshot(path=path, full_page=True)
            log.info(f"📸 Screenshot → {path}")
        except Exception:
            pass

    def find_all_rows(self, selectors: list) -> list:
        """Return list of row text lists from a table."""
        if self._driver:
            from selenium.webdriver.common.by import By
            for sel in selectors:
                try:
                    rows = self._driver.find_elements(By.CSS_SELECTOR, sel)
                    if rows:
                        return rows
                except Exception:
                    continue
        else:
            for sel in selectors:
                try:
                    rows = self._page.locator(sel).all()
                    if rows:
                        return rows
                except Exception:
                    continue
        return []


# ─────────────────────────────────────────────────────────────
#  PUBLIC API (used by main.py)
# ─────────────────────────────────────────────────────────────

def start_browser(headless: bool = True):
    """Returns a BrowserSession. Kept for backward compat with main.py."""
    session = BrowserSession()
    session.start()
    return session, None, None   # pw, browser, context placeholders


def login(session, config: dict) -> bool:
    url      = config["portal_url"]
    username = config["username"]
    password = config["password"]
    customer = config["customer_name"]

    log.info(f"🔐 Logging in to {customer} → {url}")
    session.goto(url)
    session.wait(WAIT_MS)

    user_selectors = [
        'input[type="text"]',
        'input[name="username"]',
        'input[id*="user"]',
        'input[placeholder*="username" i]',
        'input[placeholder*="vendor" i]',
        'input[placeholder*="login" i]',
    ]
    pass_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[placeholder*="password" i]',
    ]
    submit_selectors = [
        'button[type="submit"]',
        'button',
    ]

    if not session.find_and_fill(user_selectors, username, "username"):
        _screenshot(session, customer, "login_no_username")
        raise RuntimeError("Could not find username field")

    if not session.find_and_fill(pass_selectors, password, "password"):
        _screenshot(session, customer, "login_no_password")
        raise RuntimeError("Could not find password field")

    session.wait(500)
    session.click(submit_selectors)
    session.wait(WAIT_MS)

    body = session.page_text().lower()
    if any(s in body for s in ["invalid", "incorrect", "wrong", "error", "failed"]):
        _screenshot(session, customer, "login_failed")
        raise RuntimeError(f"Login failed for {customer} — check credentials")

    log.info(f"✅ Logged in → {session.current_url()}")
    return True


def extract_po_list(session, config: dict) -> list[dict]:
    customer   = config["customer_name"]
    portal_url = config["portal_url"]

    log.info(f"📋 Extracting PO list for {customer}...")

    po_paths = ["/vendor/pending-pos", "/vendor/po-confirmation", "/purchase-orders", "/po", "/orders", "/vendor/orders", "/dashboard"]
    for path in po_paths:
        try:
            session.goto(portal_url + path)
            session.wait(3000)
            if "login" not in session.current_url().lower():
                log.debug(f"PO page at {path}")
                break
        except Exception:
            continue

    session.scroll_to_bottom()
    pos = _extract_po_rows(session)

    if not pos:
        _screenshot(session, customer, "po_list_empty")
        log.warning(f"⚠️  No POs found for {customer} — check screenshot in logs/")
    else:
        log.info(f"✅ Found {len(pos)} POs")

    return pos


def extract_po_detail(session, po: dict, config: dict) -> dict:
    return po   # Detail enrichment — extend per portal as needed


# ─────────────────────────────────────────────────────────────
#  EXTRACTION HELPERS
# ─────────────────────────────────────────────────────────────

def _extract_po_rows(session) -> list[dict]:
    html = session.page_source()

    pos = _try_table_html(html)
    if pos:
        return pos

    pos = _try_generic_text(session.page_text())
    return pos


def _first_token(s: str) -> str:
    """Return just the first whitespace-separated word (useful for PO numbers with badge text)."""
    parts = s.split()
    return parts[0] if parts else s


def _try_table_html(html: str) -> list[dict]:
    """Parse PO data from HTML tables."""
    try:
        import re
        tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)
        for table in tables:
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL | re.IGNORECASE)
            if len(rows) < 2:
                continue

            def strip_tags(s):
                """Remove HTML tags and collapse whitespace."""
                text = re.sub(r'<[^>]+>', ' ', s)
                return re.sub(r'\s+', ' ', text).strip()

            headers = [strip_tags(c).lower() for c in
                       re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', rows[0], re.DOTALL | re.IGNORECASE)]

            if not any("po" in h or "order" in h or "purchase" in h for h in headers):
                continue

            pos = []
            for row in rows[1:]:
                cells = [strip_tags(c) for c in
                         re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)]
                if not cells:
                    continue
                po = _map_row(headers, cells)
                if po.get("po_number"):
                    pos.append(po)
            if pos:
                return pos
    except Exception as e:
        log.debug(f"HTML table parse failed: {e}")
    return []


def _try_generic_text(text: str) -> list[dict]:
    """Last-resort: find PO-like numbers in page text."""
    pattern = re.compile(r'\b(PO[-/]?\d{4,}|[A-Z]{2,4}-\d{4,}|\d{8,12})\b')
    matches = pattern.findall(text)
    seen, pos = set(), []
    for m in matches:
        if m not in seen:
            seen.add(m)
            pos.append({
                "po_number": m, "vendor": "", "amount": "",
                "status": "", "po_date": "", "delivery_date": "",
                "currency": "", "remarks": "", "raw": True,
            })
    return pos


def _map_row(headers: list, values: list) -> dict:
    po = {k: "" for k in ["po_number","vendor","vendor_code","amount",
                           "currency","status","po_date","delivery_date","remarks",
                           "qty","article","risk","days","category"]}
    for i, h in enumerate(headers):
        if i >= len(values):
            break
        v = values[i].strip()
        h = h.lower()
        # V2 Retail SRM portal columns + generic fallbacks
        if any(k in h for k in ["po no","po num","order no","purchase no","po number"]):
            # Extract only the leading PO number, strip trailing badge text like "QC Passed"
            raw_v = v
            # PO numbers are numeric (possibly with leading zeros); take first numeric token
            num_match = re.match(r'^(\d+)', raw_v.replace(' ', ''))
            po["po_number"] = num_match.group(1) if num_match else _first_token(raw_v)
        elif "vendor" in h and "code" in h:
            po["vendor_code"] = v
        elif "vendor" in h or "supplier" in h:
            po["vendor"] = v
        elif any(k in h for k in ["amount","value","total"]):
            po["amount"] = v
        elif "currency" in h:
            po["currency"] = v
        elif "risk" in h:
            po["risk"] = v
        elif "status" in h or "stage" in h:
            po["status"] = v
        elif "grc" in h or "delivery" in h or "due" in h:
            po["delivery_date"] = v
        elif "expected" in h or "date" in h or "created" in h:
            po["po_date"] = v
        elif "qty" in h or "quantity" in h:
            po["qty"] = v
        elif "article" in h or "design" in h:
            po["article"] = v
        elif "days" in h:
            po["days"] = v
        elif "remark" in h or "note" in h:
            po["remarks"] = v
        elif any(k in h for k in ["category","division","dept","department","segment","gender"]):
            po["category"] = v
        elif not po["po_number"] and i == 0:
            po["po_number"] = v

    # Infer category from article/vendor name if not found in columns
    if not po["category"]:
        combined = (po.get("article","") + " " + po.get("vendor","")).lower()
        if "girl" in combined:
            po["category"] = "Girls"
        elif "women" in combined or "woman" in combined or "ladies" in combined:
            po["category"] = "Women"

    return po


def _screenshot(session, customer: str, label: str):
    from pathlib import Path
    from datetime import datetime
    Path("logs").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session.screenshot(f"logs/{customer}_{label}_{ts}.png")

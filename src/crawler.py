"""
crawler.py — Login to SRM portal and extract all PO data
- Local / office machine: uses Playwright (installed via pip)
- GitHub Actions CI:       uses Selenium + system Chromium (USE_SELENIUM=true)
"""

import os
import re
import time
from src.logger import get_logger

log = get_logger()

USE_SELENIUM = os.getenv("USE_SELENIUM", "false").lower() == "true"
CHROMIUM_PATH = os.getenv("CHROMIUM_PATH", "")
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"
WAIT_AFTER_LOGIN = 4.0      # seconds
WAIT_AFTER_NAV   = 3.0
WAIT_SCROLL      = 1.5


# ─────────────────────────────────────────────────────────────
#  BROWSER SESSION — unified API over Playwright & Selenium
# ─────────────────────────────────────────────────────────────

class BrowserSession:

    def __init__(self):
        self._driver  = None   # Selenium WebDriver
        self._page    = None   # Playwright Page
        self._pw      = None
        self._browser = None
        self._context = None

    # ── Start ─────────────────────────────────────────────────

    def start(self):
        if USE_SELENIUM:
            self._start_selenium()
        else:
            self._start_playwright()
        return self

    def _start_playwright(self):
        from playwright.sync_api import sync_playwright

        # Try known Chrome/Chromium locations
        candidates = [
            "/opt/google/chrome/chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        exe = next((p for p in candidates if os.path.exists(p)), None)

        self._pw = sync_playwright().start()
        kwargs = dict(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        if exe:
            kwargs["executable_path"] = exe
            log.debug(f"Playwright using Chrome at: {exe}")

        self._browser = self._pw.chromium.launch(**kwargs)
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

        # Try explicit chromium path first
        candidates = [CHROMIUM_PATH, "/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/usr/bin/google-chrome"]
        for path in candidates:
            if path and os.path.exists(path):
                opts.binary_location = path
                log.debug(f"Selenium using Chrome at: {path}")
                break

        try:
            # Use webdriver-manager to auto-download matching chromedriver
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=opts)
        except Exception:
            # Fallback: use system chromedriver
            self._driver = webdriver.Chrome(options=opts)

        self._driver.implicitly_wait(5)
        log.info("✅ Selenium + Chromium browser started")

    # ── Stop ──────────────────────────────────────────────────

    def stop(self):
        try:
            if self._driver:  self._driver.quit()
            if self._page:    self._page.close()
            if self._context: self._context.close()
            if self._browser: self._browser.close()
            if self._pw:      self._pw.stop()
        except Exception:
            pass

    # ── Navigation ────────────────────────────────────────────

    def goto(self, url: str, timeout_ms: int = 30000):
        if self._driver:
            self._driver.get(url)
        else:
            self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def wait(self, seconds: float):
        time.sleep(seconds)

    def current_url(self) -> str:
        if self._driver:
            return self._driver.current_url
        return self._page.url

    # ── Interaction ───────────────────────────────────────────

    def find_and_fill(self, selectors: list, value: str, field: str) -> bool:
        if self._driver:
            from selenium.webdriver.common.by import By
            for sel in selectors:
                try:
                    el = self._driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(value)
                        log.debug(f"Filled {field}: {sel}")
                        return True
                except Exception:
                    continue
            # Fallback: XPath placeholder search
            for placeholder in [field, field.replace("_", " ")]:
                try:
                    el = self._driver.find_element(
                        By.XPATH, f'//input[contains(@placeholder,"{placeholder}")]'
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
                        log.debug(f"Filled {field}: {sel}")
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

    # ── Content ───────────────────────────────────────────────

    def page_text(self) -> str:
        try:
            if self._driver:
                from selenium.webdriver.common.by import By
                return self._driver.find_element(By.TAG_NAME, "body").text
            return self._page.inner_text("body")
        except Exception:
            return ""

    def page_source(self) -> str:
        try:
            if self._driver:
                return self._driver.page_source
            return self._page.content()
        except Exception:
            return ""

    def scroll_to_bottom(self, max_scrolls: int = 10):
        prev_height = 0
        for _ in range(max_scrolls):
            script = "window.scrollTo(0, document.body.scrollHeight)"
            height_script = "return document.body.scrollHeight"
            try:
                if self._driver:
                    self._driver.execute_script(script)
                    time.sleep(WAIT_SCROLL)
                    curr = self._driver.execute_script(height_script)
                else:
                    self._page.evaluate(script)
                    time.sleep(WAIT_SCROLL)
                    curr = self._page.evaluate(height_script)
                if curr == prev_height:
                    break
                prev_height = curr
            except Exception:
                break

    def screenshot(self, path: str):
        try:
            if self._driver:
                self._driver.save_screenshot(path)
            else:
                self._page.screenshot(path=path, full_page=True)
            log.info(f"📸 Screenshot → {path}")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────

def start_browser(headless: bool = True):
    session = BrowserSession()
    session.start()
    return session, None, None


def login(session: BrowserSession, config: dict) -> bool:
    url      = config["portal_url"]
    username = config["username"]
    password = config["password"]
    customer = config["customer_name"]

    log.info(f"🔐 Logging in to {customer} → {url}")
    session.goto(url)
    session.wait(WAIT_AFTER_LOGIN)

    user_selectors = [
        'input[type="text"]',
        'input[name="username"]',
        'input[name="user"]',
        'input[id*="user"]',
        'input[placeholder*="username" i]',
        'input[placeholder*="vendor" i]',
        'input[placeholder*="login" i]',
        'input[placeholder*="id" i]',
    ]
    pass_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[name="pass"]',
        'input[placeholder*="password" i]',
    ]
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'button',
    ]

    if not session.find_and_fill(user_selectors, username, "username"):
        _screenshot(session, customer, "login_no_username_field")
        raise RuntimeError(
            "Could not find username field on the portal. "
            "Check logs/ for a screenshot."
        )

    if not session.find_and_fill(pass_selectors, password, "password"):
        _screenshot(session, customer, "login_no_password_field")
        raise RuntimeError("Could not find password field on the portal.")

    session.wait(0.5)
    session.click(submit_selectors)
    session.wait(WAIT_AFTER_LOGIN)

    body = session.page_text().lower()
    fail_signals = ["invalid", "incorrect", "wrong password", "login failed", "unauthorized"]
    if any(s in body for s in fail_signals):
        _screenshot(session, customer, "login_failed")
        raise RuntimeError(f"Login failed for {customer} — portal shows error.")

    log.info(f"✅ Logged in → {session.current_url()}")
    return True


def extract_po_list(session: BrowserSession, config: dict) -> list[dict]:
    customer   = config["customer_name"]
    portal_url = config["portal_url"]

    log.info(f"📋 Extracting PO list for {customer}...")

    # Try common PO page paths
    po_paths = ["/purchase-orders", "/po", "/orders", "/vendor/orders", "/dashboard"]
    for path in po_paths:
        try:
            session.goto(portal_url + path)
            session.wait(WAIT_AFTER_NAV)
            if "login" not in session.current_url().lower():
                log.debug(f"Found PO page at: {path}")
                break
        except Exception:
            continue

    session.scroll_to_bottom()

    # Try table extraction first, then generic text
    pos = _try_table_html(session.page_source())
    if not pos:
        pos = _try_generic_text(session.page_text())

    if not pos:
        _screenshot(session, customer, "po_list_empty")
        log.warning(f"⚠️  No POs found — check logs/{customer}_po_list_empty_*.png")
    else:
        log.info(f"✅ Found {len(pos)} POs")

    return pos


def extract_po_detail(session: BrowserSession, po: dict, config: dict) -> dict:
    # Returns PO as-is; extend here for portals with detail pages
    return po


# ─────────────────────────────────────────────────────────────
#  EXTRACTION HELPERS
# ─────────────────────────────────────────────────────────────

def _try_table_html(html: str) -> list[dict]:
    try:
        tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)
        for table in tables:
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL | re.IGNORECASE)
            if len(rows) < 2:
                continue

            def strip_tags(s):
                return re.sub(r'<[^>]+>', '', s).strip()

            headers = [strip_tags(c).lower() for c in
                       re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', rows[0],
                                  re.DOTALL | re.IGNORECASE)]

            if not any("po" in h or "order" in h or "purchase" in h for h in headers):
                continue

            pos = []
            for row in rows[1:]:
                cells = [strip_tags(c) for c in
                         re.findall(r'<td[^>]*>(.*?)</td>', row,
                                    re.DOTALL | re.IGNORECASE)]
                if not cells:
                    continue
                po = _map_row(headers, cells)
                if po.get("po_number"):
                    pos.append(po)
            if pos:
                return pos
    except Exception as e:
        log.debug(f"Table parse failed: {e}")
    return []


def _try_generic_text(text: str) -> list[dict]:
    pattern = re.compile(r'\b(PO[-/]?\d{4,}|[A-Z]{2,4}-\d{4,}|\d{8,12})\b')
    seen, pos = set(), []
    for m in pattern.findall(text):
        if m not in seen:
            seen.add(m)
            pos.append({
                "po_number": m, "vendor": "", "vendor_code": "",
                "amount": "", "currency": "", "status": "",
                "po_date": "", "delivery_date": "", "remarks": "",
                "raw": True,
            })
    return pos


def _map_row(headers: list, values: list) -> dict:
    po = {k: "" for k in ["po_number", "vendor", "vendor_code", "amount",
                           "currency", "status", "po_date", "delivery_date", "remarks"]}
    for i, h in enumerate(headers):
        if i >= len(values):
            break
        v = values[i].strip()
        if any(k in h for k in ["po no", "po num", "order no", "purchase no"]):
            po["po_number"] = v
        elif "vendor" in h and "code" in h:
            po["vendor_code"] = v
        elif "vendor" in h or "supplier" in h:
            po["vendor"] = v
        elif any(k in h for k in ["amount", "value", "total"]):
            po["amount"] = v
        elif "currency" in h:
            po["currency"] = v
        elif "status" in h:
            po["status"] = v
        elif "delivery" in h or "due" in h:
            po["delivery_date"] = v
        elif "date" in h or "created" in h:
            po["po_date"] = v
        elif "remark" in h or "note" in h:
            po["remarks"] = v
        elif not po["po_number"] and i == 0:
            po["po_number"] = v
    return po


def _screenshot(session: BrowserSession, customer: str, label: str):
    from pathlib import Path
    from datetime import datetime
    Path("logs").mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session.screenshot(f"logs/{customer}_{label}_{ts}.png")

# SRM Portal PO Automation

Automatically monitors your customer SRM portals for Purchase Order changes and syncs them to Google Sheets. Detects new POs, cancellations, and field updates — with email and Slack alerts.

---

## What it does

- Logs into your SRM portals automatically
- Scrapes all current Purchase Orders
- Compares against the last run (detects new / cancelled / updated POs)
- Validates data accuracy before writing anything
- Writes changes to a Google Sheet (colour coded: 🟢 new, 🟡 updated, 🔴 cancelled)
- Sends Slack and/or email alerts with a summary
- Runs on a schedule — every customer on its own interval

---

## Project structure

```
srm-automation/
├── main.py                  ← Run manually or via GitHub Actions
├── scheduler.py             ← Keep running locally on a schedule
├── requirements.txt
├── .env                     ← Your credentials (never commit!)
├── .env.example             ← Template — safe to commit
├── .gitignore
│
├── customers/
│   └── v2retail.yaml        ← One file per customer
│
├── src/
│   ├── config.py            ← Loads YAML + env vars
│   ├── crawler.py           ← Playwright login + PO extraction
│   ├── diff.py              ← Compare snapshots, detect changes
│   ├── validator.py         ← Accuracy checks before writing
│   ├── sheets.py            ← Google Sheets API writer
│   ├── notifications.py     ← Slack + email alerts
│   └── logger.py            ← Coloured logging + log files
│
├── snapshots/               ← Auto-created: per-run PO snapshots (JSON)
├── logs/                    ← Auto-created: run logs + debug screenshots
│
└── .github/
    └── workflows/
        └── srm_automation.yml  ← GitHub Actions (cloud schedule)
```

---

## Setup — Step by Step

### Step 1 — Install Python

Download Python 3.11+ from https://python.org and install it.

Verify:
```bash
python --version
# Should print Python 3.11.x or higher
```

### Step 2 — Download the project

```bash
# Option A: Clone from Git
git clone https://github.com/yourcompany/srm-automation.git
cd srm-automation

# Option B: Just download the ZIP and unzip it
cd srm-automation
```

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### Step 4 — Configure credentials

Copy the template and fill in your values:
```bash
cp .env.example .env
```

Open `.env` in any text editor (Notepad, VS Code, etc.) and fill in:
```
V2RETAIL_SRM_URL=https://srm.v2retail.com
V2RETAIL_SRM_USER=0000203622
V2RETAIL_SRM_PASS=Deepak@123
V2RETAIL_SHEET_ID=<paste your Google Sheet ID here>
```

**How to find your Google Sheet ID:**
Open the sheet → look at the URL:
`https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`
Copy just the long string between `/d/` and `/edit`.

### Step 5 — Set up Google Sheets access

1. Go to https://console.cloud.google.com
2. Create a new project (name it "SRM Automation")
3. Click "APIs & Services" → "Enable APIs"
4. Search for "Google Sheets API" → Enable it
5. Click "Credentials" → "Create Credentials" → "Service Account"
6. Fill in any name → click through → Done
7. Click your new service account → "Keys" tab → "Add Key" → JSON
8. Download the JSON file → rename it `service-account.json`
9. Place it in the project root folder

10. **Share your Google Sheet with the service account:**
    - Open the downloaded JSON file
    - Find the `client_email` field (looks like `something@project.iam.gserviceaccount.com`)
    - Open your Google Sheet → Share → paste that email → Editor access

### Step 6 — Test the login

```bash
python main.py --test-login --customer v2retail
```

You should see:
```
✅ Logged in successfully
✅ Login test passed
```

If login fails, it saves a screenshot to `logs/` so you can see what went wrong.

### Step 7 — Run a dry run (no sheet writes)

```bash
python main.py --dry-run --customer v2retail
```

This crawls the portal, detects changes, and shows you what WOULD be written — without actually touching your sheet.

### Step 8 — Run for real

```bash
python main.py --customer v2retail
```

Or for all customers:
```bash
python main.py
```

### Step 9 — Keep it running automatically

**Option A: On your own computer (runs while computer is on):**
```bash
python scheduler.py
```
Leave this terminal window open. It checks every 2 hours automatically.

**Option B: GitHub Actions (runs in the cloud, free):**
1. Push code to a GitHub repo
2. Go to repo Settings → Secrets → Actions
3. Add each variable from your `.env` as a Secret
4. For `GOOGLE_SERVICE_ACCOUNT_JSON`: paste the entire contents of your `service-account.json` file
5. The workflow runs automatically every 2 hours on weekdays

---

## Adding a new customer

### 1. Add credentials to `.env`
```bash
NEWCUSTOMER_SRM_URL=https://portal.newcustomer.com
NEWCUSTOMER_SRM_USER=your.login
NEWCUSTOMER_SRM_PASS=your.password
NEWCUSTOMER_SHEET_ID=your_sheet_id
```

### 2. Create `customers/newcustomer.yaml`
```yaml
customer_id: newcustomer
customer_name: New Customer Ltd

portal_url_env: NEWCUSTOMER_SRM_URL
auth:
  username_env: NEWCUSTOMER_SRM_USER
  password_env: NEWCUSTOMER_SRM_PASS

google_sheet:
  sheet_id_env: NEWCUSTOMER_SHEET_ID
  tab_name: PO Tracker
  columns:
    po_number: A
    vendor_code: B
    vendor_name: C
    po_date: D
    delivery_date: E
    amount: F
    currency: G
    status: H
    remarks: I
    last_synced: J

crawler:
  po_list_path: /purchase-orders
  wait_after_login_ms: 3000
  wait_after_navigate_ms: 2000
  scroll_to_load: true
  max_pages: 50

schedule:
  interval_minutes: 120
  active_hours_start: "07:00"
  active_hours_end: "21:00"
  timezone: Asia/Kolkata
  jitter_seconds: 90

validation:
  required_fields:
    - po_number
    - status

notifications:
  on_new_po: true
  on_cancelled_po: true
  on_updated_po: true
  channels:
    - slack
    - email
```

### 3. Test it
```bash
python main.py --test-login --customer newcustomer
python main.py --dry-run --customer newcustomer
python main.py --customer newcustomer
```

That's it. No code changes needed.

---

## Troubleshooting

### Login fails
- Check credentials in `.env`
- Run with `HEADLESS=false` in `.env` to watch the browser
- Check screenshot in `logs/`
- Some portals block headless browsers — the crawler already sets a real browser user-agent

### No POs extracted
- The crawler uses multiple strategies to find PO data
- Open the portal manually and press F12 → inspect the PO table HTML
- Share the HTML structure and we can update the selectors in `src/crawler.py`

### Google Sheet not updating
- Check `GOOGLE_SERVICE_ACCOUNT_FILE` points to your JSON file
- Check the service account email has Editor access on the sheet
- Check `V2RETAIL_SHEET_ID` is the correct ID from the sheet URL
- Changes are saved to `logs/` as a fallback if Sheets fails

### Want to watch it run?
Set `HEADLESS=false` in your `.env` — the browser window opens and you can watch every step.

---

## Commands reference

| Command | What it does |
|---------|-------------|
| `python main.py` | Run all customers |
| `python main.py --customer v2retail` | Run one customer |
| `python main.py --test-login` | Test login only |
| `python main.py --dry-run` | Crawl + diff, no writes |
| `python scheduler.py` | Keep running on a schedule |

---

## Google Sheet colour coding

| Colour | Meaning |
|--------|---------|
| 🟢 Light green | New PO added |
| 🟡 Light amber | PO fields updated |
| 🔴 Light red | PO cancelled |
| White | Unchanged |

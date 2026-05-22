"""
notifications.py — Send alerts via Slack and/or Email
Triggered after every run that has changes
"""

import os
import json
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from src.logger import get_logger

log = get_logger()


def send_alerts(changes: dict, config: dict):
    """Send notifications if any PO changes were detected."""
    new_count       = len(changes.get("new", []))
    cancelled_count = len(changes.get("cancelled", []))
    updated_count   = len(changes.get("updated", []))

    if new_count + cancelled_count + updated_count == 0:
        log.debug("No changes — skipping notifications")
        return

    notif_cfg  = config.get("notifications", {})
    channels   = notif_cfg.get("channels", [])
    customer   = config["customer_name"]

    # Build the message
    subject, plain_text, html_body = _build_message(changes, config)

    log.info(f"📣 Sending notifications for {customer}...")

    if "slack" in channels:
        _send_slack(changes, config, subject)

    if "email" in channels:
        _send_email(subject, plain_text, html_body, config)

    log.info("✅ Notifications sent")


# ─────────────────────────────────────────────────────────────
#  MESSAGE BUILDER
# ─────────────────────────────────────────────────────────────

def _build_message(changes: dict, config: dict) -> tuple[str, str, str]:
    customer = config["customer_name"]
    now      = datetime.now().strftime("%d %b %Y %I:%M %p")

    new_pos       = changes.get("new", [])
    cancelled_pos = changes.get("cancelled", [])
    updated_pos   = changes.get("updated", [])

    # Subject
    parts = []
    if new_pos:
        parts.append(f"{len(new_pos)} New")
    if cancelled_pos:
        parts.append(f"{len(cancelled_pos)} Cancelled")
    if updated_pos:
        parts.append(f"{len(updated_pos)} Updated")
    subject = f"[SRM Alert] {customer} — {', '.join(parts)} PO(s) — {now}"

    # ── Plain text ──
    lines = [
        f"SRM PO Alert — {customer}",
        f"Run at: {now}",
        "=" * 50,
    ]

    if new_pos:
        lines.append(f"\n🆕 NEW POs ({len(new_pos)}):")
        for po in new_pos:
            lines.append(
                f"  • {po.get('po_number','?')} | "
                f"{po.get('vendor') or po.get('vendor_name','?')} | "
                f"{po.get('currency','')} {po.get('amount','?')} | "
                f"Status: {po.get('status','?')}"
            )

    if cancelled_pos:
        lines.append(f"\n❌ CANCELLED POs ({len(cancelled_pos)}):")
        for po in cancelled_pos:
            lines.append(
                f"  • {po.get('po_number','?')} | "
                f"{po.get('vendor') or po.get('vendor_name','?')}"
            )

    if updated_pos:
        lines.append(f"\n✏️  UPDATED POs ({len(updated_pos)}):")
        for upd in updated_pos:
            lines.append(f"  • PO {upd['po_number']}:")
            for chg in upd.get("changes", []):
                flag = " ⚠️ LARGE CHANGE" if chg.get("large_change") else ""
                lines.append(
                    f"      {chg['field']}: "
                    f"'{chg['old']}' → '{chg['new']}'{flag}"
                )

    plain_text = "\n".join(lines)

    # ── HTML ──
    html_body = _build_html(customer, now, new_pos, cancelled_pos, updated_pos)

    return subject, plain_text, html_body


def _build_html(customer, now, new_pos, cancelled_pos, updated_pos) -> str:
    def po_rows(pos_list, color):
        rows = ""
        for po in pos_list:
            rows += f"""
            <tr style="background:{color}">
              <td style="padding:6px 10px;border:1px solid #ddd">{po.get('po_number','')}</td>
              <td style="padding:6px 10px;border:1px solid #ddd">{po.get('vendor') or po.get('vendor_name','')}</td>
              <td style="padding:6px 10px;border:1px solid #ddd">{po.get('currency','')} {po.get('amount','')}</td>
              <td style="padding:6px 10px;border:1px solid #ddd">{po.get('status','')}</td>
            </tr>"""
        return rows

    new_section = ""
    if new_pos:
        new_section = f"""
        <h3 style="color:#2e7d32">🆕 New POs ({len(new_pos)})</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px">
          <tr style="background:#333;color:#fff">
            <th style="padding:8px 10px;text-align:left">PO Number</th>
            <th style="padding:8px 10px;text-align:left">Vendor</th>
            <th style="padding:8px 10px;text-align:left">Amount</th>
            <th style="padding:8px 10px;text-align:left">Status</th>
          </tr>
          {po_rows(new_pos,'#f1f8e9')}
        </table>"""

    cancelled_section = ""
    if cancelled_pos:
        cancelled_section = f"""
        <h3 style="color:#c62828">❌ Cancelled POs ({len(cancelled_pos)})</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px">
          <tr style="background:#333;color:#fff">
            <th style="padding:8px 10px;text-align:left">PO Number</th>
            <th style="padding:8px 10px;text-align:left">Vendor</th>
            <th style="padding:8px 10px;text-align:left">Amount</th>
            <th style="padding:8px 10px;text-align:left">Status</th>
          </tr>
          {po_rows(cancelled_pos,'#ffebee')}
        </table>"""

    updated_section = ""
    if updated_pos:
        rows = ""
        for upd in updated_pos:
            for chg in upd.get("changes", []):
                flag = " ⚠️" if chg.get("large_change") else ""
                rows += f"""
                <tr style="background:#fffde7">
                  <td style="padding:6px 10px;border:1px solid #ddd">{upd['po_number']}</td>
                  <td style="padding:6px 10px;border:1px solid #ddd">{chg['field']}</td>
                  <td style="padding:6px 10px;border:1px solid #ddd">{chg['old']}</td>
                  <td style="padding:6px 10px;border:1px solid #ddd">{chg['new']}{flag}</td>
                </tr>"""
        updated_section = f"""
        <h3 style="color:#e65100">✏️ Updated POs ({len(updated_pos)})</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px">
          <tr style="background:#333;color:#fff">
            <th style="padding:8px 10px;text-align:left">PO Number</th>
            <th style="padding:8px 10px;text-align:left">Field</th>
            <th style="padding:8px 10px;text-align:left">Old Value</th>
            <th style="padding:8px 10px;text-align:left">New Value</th>
          </tr>
          {rows}
        </table>"""

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;padding:20px">
      <div style="background:#1a1a2e;color:#fff;padding:16px 20px;border-radius:8px 8px 0 0">
        <h2 style="margin:0">🔔 SRM PO Alert — {customer}</h2>
        <p style="margin:4px 0 0;font-size:13px;opacity:.8">Run at {now}</p>
      </div>
      <div style="border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
        {new_section}
        {cancelled_section}
        {updated_section}
        <p style="font-size:11px;color:#999;margin-top:24px">
          This is an automated message from SRM PO Automation.
        </p>
      </div>
    </body></html>"""


# ─────────────────────────────────────────────────────────────
#  SLACK
# ─────────────────────────────────────────────────────────────

def _send_slack(changes: dict, config: dict, subject: str):
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        log.debug("Slack webhook not configured — skipping")
        return

    customer      = config["customer_name"]
    new_pos       = changes.get("new", [])
    cancelled_pos = changes.get("cancelled", [])
    updated_pos   = changes.get("updated", [])

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔔 SRM Alert — {customer}"}
        },
    ]

    if new_pos:
        text = "\n".join(
            f"• *{p.get('po_number','?')}* — "
            f"{p.get('vendor') or p.get('vendor_name','?')} — "
            f"{p.get('currency','')} {p.get('amount','?')}"
            for p in new_pos
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🆕 New POs ({len(new_pos)})*\n{text}"}
        })

    if cancelled_pos:
        text = "\n".join(
            f"• *{p.get('po_number','?')}* — {p.get('vendor') or p.get('vendor_name','?')}"
            for p in cancelled_pos
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*❌ Cancelled ({len(cancelled_pos)})*\n{text}"}
        })

    if updated_pos:
        text = "\n".join(
            f"• *{u['po_number']}* — "
            + ", ".join(
                f"{c['field']}: `{c['old']}` → `{c['new']}`"
                + (" ⚠️" if c.get("large_change") else "")
                for c in u.get("changes", [])
            )
            for u in updated_pos
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*✏️ Updated ({len(updated_pos)})*\n{text}"}
        })

    try:
        resp = requests.post(
            webhook,
            json={"blocks": blocks, "text": subject},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("✅ Slack notification sent")
        else:
            log.warning(f"Slack returned {resp.status_code}: {resp.text}")
    except Exception as e:
        log.warning(f"Slack send failed: {e}")


# ─────────────────────────────────────────────────────────────
#  EMAIL
# ─────────────────────────────────────────────────────────────

def _send_email(subject: str, plain_text: str, html_body: str, config: dict):
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    to_addr   = os.getenv("ALERT_EMAIL_TO", "").strip()

    if not all([smtp_host, smtp_user, smtp_pass, to_addr]):
        log.debug("Email not fully configured — skipping")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = to_addr

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_addr], msg.as_string())
        log.info(f"✅ Email sent to {to_addr}")
    except Exception as e:
        log.warning(f"Email send failed: {e}")

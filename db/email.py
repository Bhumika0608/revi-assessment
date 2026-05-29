"""
Order receipt emails via Gmail SMTP.

Setup (one-time):
  1. Enable 2-Step Verification on your Google account.
  2. Go to myaccount.google.com/apppasswords → create an app password.
  3. Add to .env:
       GMAIL_SENDER=you@gmail.com
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

Card data is never passed here — only order metadata.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from db.restaurant import (
    FOOTER_LINE,
    LOCATION_TAGLINE,
    NAME as RESTAURANT_NAME,
    PHONE,
    PHONE_TEL,
    SHORT_LOCATION,
)


def _get_credentials() -> tuple[str, str] | None:
    sender   = os.getenv("GMAIL_SENDER", "")
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not sender or not password:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            sender   = os.getenv("GMAIL_SENDER", "")
            password = os.getenv("GMAIL_APP_PASSWORD", "")
        except ImportError:
            pass
    if sender and password:
        return sender, password
    return None


def send_order_receipt(
    *,
    to_email: str,
    order_id: str,
    items: list[dict],
    breakdown: dict,
    fulfillment_type: str,
    eta: str,
    transaction_id: str,
    delivery_address: str = "",
) -> dict:
    """
    Send a plain-text + HTML order receipt.

    Returns:
        {"sent": bool, "error": str | None}
    """
    creds = _get_credentials()
    if not creds:
        return {"sent": False, "error": "GMAIL_SENDER or GMAIL_APP_PASSWORD not set in .env"}

    sender, password = creds

    subject = f"Your {RESTAURANT_NAME} order is confirmed! ({order_id})"
    plain   = _build_plain(order_id, items, breakdown, fulfillment_type, eta,
                           transaction_id, delivery_address)
    html    = _build_html(order_id, items, breakdown, fulfillment_type, eta,
                          transaction_id, delivery_address)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{RESTAURANT_NAME} <{sender}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(sender, password)
            server.sendmail(sender, to_email, msg.as_string())
        return {"sent": True, "error": None}
    except Exception as exc:
        return {"sent": False, "error": str(exc)}


# ── Email body builders ───────────────────────────────────────────────────────

def _build_plain(
    order_id, items, breakdown, fulfillment_type, eta,
    transaction_id, delivery_address,
) -> str:
    ft_label = "Pickup" if fulfillment_type == "pickup" else "Delivery"
    lines = [
        f"{RESTAURANT_NAME} — Order Confirmation",
        "=" * 40,
        f"Order ID:    {order_id}",
        f"Fulfillment: {ft_label}",
        f"ETA:         {eta}",
        f"Transaction: {transaction_id}",
        "",
        "YOUR ORDER",
        "-" * 40,
    ]
    for item in items:
        mods = ", ".join(item.get("modifiers", [])) or None
        line = f"  {item['quantity']}x {item['name']}  ${item['line_total']:.2f}"
        if mods:
            line += f"\n      Modifiers: {mods}"
        lines.append(line)

    lines += [
        "",
        "-" * 40,
        f"Subtotal:     ${breakdown.get('subtotal', 0):.2f}",
    ]
    if fulfillment_type == "delivery" and breakdown.get("delivery_fee", 0) > 0:
        lines.append(f"Delivery fee: ${breakdown['delivery_fee']:.2f}")
    lines += [
        f"Tax (8%):     ${breakdown.get('tax', 0):.2f}",
        f"Total paid:   ${breakdown.get('total', 0):.2f}",
        "",
        "-" * 40,
    ]
    if fulfillment_type == "pickup":
        lines.append(f"Pick up at: {SHORT_LOCATION}")
    elif delivery_address:
        lines.append(f"Delivering to: {delivery_address}")
    lines += [
        "",
        f"Questions? Call {PHONE}",
        FOOTER_LINE,
    ]
    return "\n".join(lines)


def _build_html(
    order_id, items, breakdown, fulfillment_type, eta,
    transaction_id, delivery_address,
) -> str:
    ft_label = "Pickup" if fulfillment_type == "pickup" else "Delivery"

    item_rows = ""
    for item in items:
        mods = ", ".join(item.get("modifiers", [])) or ""
        mod_html = f"<br><small style='color:#888'>{mods}</small>" if mods else ""
        item_rows += f"""
        <tr>
          <td style='padding:6px 0'>{item['quantity']}× <strong>{item['name']}</strong>{mod_html}</td>
          <td style='padding:6px 0;text-align:right'>${item['line_total']:.2f}</td>
        </tr>"""

    delivery_row = ""
    if fulfillment_type == "delivery" and breakdown.get("delivery_fee", 0) > 0:
        delivery_row = f"""
        <tr>
          <td style='padding:4px 0;color:#555'>Delivery fee</td>
          <td style='padding:4px 0;text-align:right;color:#555'>${breakdown['delivery_fee']:.2f}</td>
        </tr>"""

    location_line = (
        f"📍 Pick up at: <strong>{SHORT_LOCATION}</strong>"
        if fulfillment_type == "pickup"
        else f"🚚 Delivering to: <strong>{delivery_address}</strong>"
    )

    return f"""
<!DOCTYPE html>
<html>
<body style='font-family:sans-serif;max-width:520px;margin:0 auto;padding:24px;color:#222'>

  <h2 style='color:#e05c2a;margin-bottom:4px'>🌮 {RESTAURANT_NAME}</h2>
  <p style='color:#555;margin-top:0'>{LOCATION_TAGLINE}</p>

  <hr style='border:none;border-top:1px solid #eee'>

  <h3 style='margin-bottom:8px'>✅ Order Confirmed!</h3>

  <table style='width:100%;font-size:14px'>
    <tr><td style='color:#555'>Order ID</td>    <td style='text-align:right'><code>{order_id}</code></td></tr>
    <tr><td style='color:#555'>Fulfillment</td> <td style='text-align:right'>{ft_label}</td></tr>
    <tr><td style='color:#555'>ETA</td>         <td style='text-align:right'>{eta}</td></tr>
    <tr><td style='color:#555'>Transaction</td> <td style='text-align:right'><code>{transaction_id}</code></td></tr>
  </table>

  <hr style='border:none;border-top:1px solid #eee;margin:16px 0'>
  <h4 style='margin-bottom:8px'>Your Order</h4>

  <table style='width:100%;font-size:14px'>
    {item_rows}
    <tr><td colspan='2'><hr style='border:none;border-top:1px solid #eee'></td></tr>
    <tr>
      <td style='padding:4px 0;color:#555'>Subtotal</td>
      <td style='padding:4px 0;text-align:right;color:#555'>${breakdown.get('subtotal', 0):.2f}</td>
    </tr>
    {delivery_row}
    <tr>
      <td style='padding:4px 0;color:#555'>Tax (8% Miami-Dade)</td>
      <td style='padding:4px 0;text-align:right;color:#555'>${breakdown.get('tax', 0):.2f}</td>
    </tr>
    <tr>
      <td style='padding:8px 0'><strong>Total paid</strong></td>
      <td style='padding:8px 0;text-align:right'><strong>${breakdown.get('total', 0):.2f}</strong></td>
    </tr>
  </table>

  <hr style='border:none;border-top:1px solid #eee;margin:16px 0'>
  <p style='font-size:14px'>{location_line}</p>

  <p style='font-size:13px;color:#888;margin-top:24px'>
    Questions? Call <a href='tel:{PHONE_TEL}'>{PHONE}</a>
  </p>

</body>
</html>"""

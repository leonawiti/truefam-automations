"""
Website Health Monitor

Checks a list of websites for health (HTTP status, SSL, response time,
content sanity) and emails a status report. If any site is broken, the
report includes a diagnosis and a suggested fix.

Scheduled daily at 8:00 AM Central via launchd.

Usage:
    python health_check.py             # Run check + send email
    python health_check.py --no-email  # Run check, print to stdout only
    python health_check.py --force-fail truefamwel.com  # Test broken-site path
"""

from __future__ import annotations

import argparse
import os
import smtplib
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# ── Paths and config ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Sites to monitor (URL → optional sanity-check string the page must contain)
SITES = [
    {
        "url": "https://truefamwel.com",
        "must_contain": None,  # any 200 is healthy
        "name": "TRUEFAM Welfare (prod)",
    },
    {
        "url": "https://stage.truefamwel.com",
        "must_contain": None,
        "name": "TRUEFAM Welfare (staging)",
    },
    {
        "url": "https://epicbizintel.com",
        "must_contain": None,
        "name": "Epic Biz Intel",
    },
    {
        "url": "https://hibglobal.org",
        "must_contain": None,
        "name": "HIB Global",
    },
]

# Thresholds
TIMEOUT_SECONDS = 15
SLOW_THRESHOLD_SECONDS = 5
SSL_WARN_DAYS = 14   # warn if SSL expires within 2 weeks

# Email configuration
RECIPIENT = os.getenv("HEALTH_REPORT_RECIPIENT", "lawiti@truefamwel.com")
SENDER_EMAIL = os.getenv("GMAIL_ADDRESS", "")
SENDER_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ─────────────────────────────────────────────────────────────────────────────
# Health check logic
# ─────────────────────────────────────────────────────────────────────────────

def check_ssl_certificate(hostname: str) -> dict:
    """Return SSL cert info: days_until_expiry, issuer, error."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = (not_after - datetime.now(timezone.utc)).days
        issuer = dict(x[0] for x in cert["issuer"]).get("organizationName", "Unknown")
        return {"days_until_expiry": days_left, "issuer": issuer, "error": None}
    except Exception as e:
        return {"days_until_expiry": None, "issuer": None, "error": str(e)}


def check_dns(hostname: str) -> dict:
    """Resolve hostname to IP."""
    try:
        ip = socket.gethostbyname(hostname)
        return {"ip": ip, "error": None}
    except Exception as e:
        return {"ip": None, "error": str(e)}


def check_site(site: dict) -> dict:
    """Run all health checks on one site. Returns a result dict."""
    url = site["url"]
    parsed = urlparse(url)
    hostname = parsed.hostname
    result = {
        "name": site["name"],
        "url": url,
        "hostname": hostname,
        "healthy": False,
        "status_code": None,
        "response_time_ms": None,
        "dns": None,
        "ssl": None,
        "issue": None,
        "suggestion": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1. DNS resolution
    dns = check_dns(hostname)
    result["dns"] = dns
    if dns["error"]:
        result["issue"] = f"DNS resolution failed: {dns['error']}"
        result["suggestion"] = (
            "Check the domain's DNS records at your registrar. Verify A/AAAA "
            "records point to the correct server IP. If you recently moved "
            "hosting, propagation can take up to 48 hours."
        )
        return result

    # 2. SSL certificate (only for https)
    if parsed.scheme == "https":
        ssl_info = check_ssl_certificate(hostname)
        result["ssl"] = ssl_info
        if ssl_info["error"]:
            result["issue"] = f"SSL handshake failed: {ssl_info['error']}"
            result["suggestion"] = (
                "Check that the SSL certificate is installed and valid. "
                "If using Let's Encrypt, run `certbot renew` on the server. "
                "If using Cloudflare, ensure SSL/TLS mode is 'Full' or "
                "'Full (strict)'."
            )
            return result

    # 3. HTTP request
    try:
        start = time.monotonic()
        response = requests.get(
            url,
            timeout=TIMEOUT_SECONDS,
            allow_redirects=True,
            headers={"User-Agent": "TrueFam-Health-Monitor/1.0"},
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result["status_code"] = response.status_code
        result["response_time_ms"] = elapsed_ms

        # Diagnose based on status code
        if response.status_code == 200:
            # Optional content check
            if site.get("must_contain") and site["must_contain"] not in response.text:
                result["issue"] = (
                    f"Page returned 200 but expected content "
                    f"'{site['must_contain']}' not found"
                )
                result["suggestion"] = (
                    "The site is responding but may be serving an error page "
                    "or wrong content. Check the upstream app (Next.js / "
                    "Django) is running and returning the right pages."
                )
                return result
            result["healthy"] = True
            # SSL expiry warning even when healthy
            if (result["ssl"] and result["ssl"]["days_until_expiry"] is not None
                    and result["ssl"]["days_until_expiry"] < SSL_WARN_DAYS):
                result["issue"] = (
                    f"SSL cert expires in {result['ssl']['days_until_expiry']} days"
                )
                result["suggestion"] = "Renew the SSL certificate soon (run `certbot renew` or your renewal process)."
                result["healthy"] = False  # treat as broken so it gets attention
            elif elapsed_ms > SLOW_THRESHOLD_SECONDS * 1000:
                result["issue"] = f"Slow response: {elapsed_ms}ms (threshold: {SLOW_THRESHOLD_SECONDS}s)"
                result["suggestion"] = (
                    "Site is up but slow. Check server load (CPU/memory), "
                    "database query times, and cache hit rates. Consider "
                    "enabling a CDN or optimizing slow endpoints."
                )
        elif response.status_code == 502:
            result["issue"] = "502 Bad Gateway"
            result["suggestion"] = (
                "Nginx (or your reverse proxy) cannot reach the upstream "
                "application. Check that your backend process (gunicorn / "
                "node / pm2) is running on the server. Run "
                "`systemctl status <service>` or `pm2 list` and restart if "
                "needed."
            )
        elif response.status_code == 503:
            result["issue"] = "503 Service Unavailable"
            result["suggestion"] = (
                "The server is overloaded or in maintenance mode. Check "
                "server load and disable maintenance mode if applicable."
            )
        elif response.status_code == 504:
            result["issue"] = "504 Gateway Timeout"
            result["suggestion"] = (
                "The upstream application took too long to respond. Check "
                "for hung database queries, deadlocks, or a stuck process. "
                "Restart the backend service if needed."
            )
        elif 500 <= response.status_code < 600:
            result["issue"] = f"Server error: HTTP {response.status_code}"
            result["suggestion"] = (
                "Check the application's error logs for stack traces. "
                "Look in /var/log/ on the server or your hosting platform's "
                "log viewer."
            )
        elif response.status_code == 404:
            result["issue"] = "404 Not Found"
            result["suggestion"] = (
                "The homepage URL returned 404. Check your routing config "
                "(Next.js pages/, Django URLconf, Nginx location blocks)."
            )
        elif response.status_code == 403:
            result["issue"] = "403 Forbidden"
            result["suggestion"] = (
                "Access denied. Check file permissions on the web root and "
                "any IP-based access rules in Nginx or Cloudflare."
            )
        elif 400 <= response.status_code < 500:
            result["issue"] = f"Client error: HTTP {response.status_code}"
            result["suggestion"] = (
                "Investigate why the homepage is returning a 4xx. Check "
                "Nginx config, redirect rules, and any auth middleware."
            )
        else:
            result["issue"] = f"Unexpected status: HTTP {response.status_code}"
            result["suggestion"] = "Investigate why the site is not returning HTTP 200."

    except requests.exceptions.SSLError as e:
        result["issue"] = f"SSL error: {e}"
        result["suggestion"] = (
            "Certificate is invalid, expired, or mismatched. Renew the cert "
            "or fix the SSL configuration on the server."
        )
    except requests.exceptions.ConnectTimeout:
        result["issue"] = f"Connection timeout (>{TIMEOUT_SECONDS}s)"
        result["suggestion"] = (
            "Server is not accepting connections. Check that the server is "
            "powered on, the firewall allows port 443, and Nginx is running. "
            "Try `systemctl status nginx` on the server."
        )
    except requests.exceptions.ConnectionError as e:
        result["issue"] = f"Connection refused: {e}"
        result["suggestion"] = (
            "Connection was actively refused. The web server (Nginx/Apache) "
            "is likely stopped. SSH into the server and start it with "
            "`systemctl start nginx`."
        )
    except requests.exceptions.ReadTimeout:
        result["issue"] = f"Read timeout (>{TIMEOUT_SECONDS}s)"
        result["suggestion"] = (
            "Server accepted the connection but didn't send a response in "
            "time. Backend application is likely hung. Check process status "
            "and restart it."
        )
    except Exception as e:
        result["issue"] = f"Unexpected error: {type(e).__name__}: {e}"
        result["suggestion"] = "Investigate this unusual failure manually."

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Email report
# ─────────────────────────────────────────────────────────────────────────────

def render_html_report(results: list[dict]) -> tuple[str, str]:
    """Return (subject, html_body) for the email."""
    healthy_count = sum(1 for r in results if r["healthy"])
    broken_count = len(results) - healthy_count
    overall = "✅ ALL HEALTHY" if broken_count == 0 else f"⚠️ {broken_count} BROKEN"

    subject = f"[TrueFam Health] {overall} — {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}"

    rows_html = ""
    for r in results:
        if r["healthy"]:
            badge = '<span style="background:#16a34a;color:#fff;padding:4px 12px;border-radius:4px;font-weight:600;">HEALTHY</span>'
            details = f"<div style='color:#666;font-size:13px;margin-top:6px;'>HTTP {r['status_code']} • {r['response_time_ms']}ms"
            if r["ssl"] and r["ssl"]["days_until_expiry"] is not None:
                details += f" • SSL expires in {r['ssl']['days_until_expiry']} days"
            details += "</div>"
            issue_block = ""
        else:
            badge = '<span style="background:#dc2626;color:#fff;padding:4px 12px;border-radius:4px;font-weight:600;">BROKEN</span>'
            details_parts = []
            if r["status_code"] is not None:
                details_parts.append(f"HTTP {r['status_code']}")
            if r["response_time_ms"] is not None:
                details_parts.append(f"{r['response_time_ms']}ms")
            if r["dns"] and r["dns"]["ip"]:
                details_parts.append(f"IP {r['dns']['ip']}")
            details = f"<div style='color:#666;font-size:13px;margin-top:6px;'>{' • '.join(details_parts) or 'No response'}</div>"
            issue_block = f"""
            <div style='margin-top:14px;padding:12px;background:#fef2f2;border-left:4px solid #dc2626;border-radius:4px;'>
                <div style='font-weight:600;color:#991b1b;margin-bottom:6px;'>Issue:</div>
                <div style='color:#7f1d1d;'>{r['issue']}</div>
                <div style='font-weight:600;color:#991b1b;margin-top:10px;margin-bottom:6px;'>Suggested fix:</div>
                <div style='color:#7f1d1d;'>{r['suggestion']}</div>
            </div>
            """
        rows_html += f"""
        <tr><td style='padding:18px 0;border-bottom:1px solid #e5e7eb;'>
            <div style='display:flex;align-items:center;justify-content:space-between;gap:12px;'>
                <div>
                    <div style='font-size:18px;font-weight:600;color:#111827;'>{r['name']}</div>
                    <div style='color:#6b7280;font-size:14px;margin-top:2px;'>
                        <a href='{r['url']}' style='color:#2563eb;text-decoration:none;'>{r['url']}</a>
                    </div>
                </div>
                <div>{badge}</div>
            </div>
            {details}
            {issue_block}
        </td></tr>
        """

    html = f"""<!DOCTYPE html>
<html><body style='font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f3f4f6;margin:0;padding:24px;'>
    <div style='max-width:640px;margin:0 auto;background:#fff;border-radius:8px;padding:32px;box-shadow:0 1px 3px rgba(0,0,0,0.1);'>
        <h1 style='margin:0 0 8px 0;font-size:24px;color:#111827;'>TRUEFAM Site Health Report</h1>
        <p style='color:#6b7280;margin:0 0 24px 0;'>
            {datetime.now().strftime('%A, %B %d, %Y at %I:%M %p %Z')}<br>
            Overall: <strong>{healthy_count}/{len(results)} healthy</strong>
        </p>
        <table style='width:100%;border-collapse:collapse;'>{rows_html}</table>
        <p style='margin-top:24px;color:#9ca3af;font-size:12px;text-align:center;'>
            Automated daily report from website-health-monitor at 8:00 AM Central
        </p>
    </div>
</body></html>"""

    return subject, html


def render_text_report(results: list[dict]) -> str:
    """Plain-text fallback for the email body."""
    lines = ["TRUEFAM Site Health Report", "=" * 60, ""]
    for r in results:
        status = "HEALTHY" if r["healthy"] else "BROKEN"
        lines.append(f"[{status}] {r['name']} — {r['url']}")
        if r["status_code"] is not None:
            lines.append(f"  HTTP {r['status_code']} ({r['response_time_ms']}ms)")
        if not r["healthy"]:
            lines.append(f"  Issue: {r['issue']}")
            lines.append(f"  Fix:   {r['suggestion']}")
        lines.append("")
    return "\n".join(lines)


def send_email(subject: str, html_body: str, text_body: str) -> None:
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        raise RuntimeError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"TrueFam Health Monitor <{SENDER_EMAIL}>"
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def log_run(message: str) -> None:
    log_file = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"
    log_file.write_text(log_file.read_text() + line if log_file.exists() else line)
    print(line, end="")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-email", action="store_true", help="Print to stdout, don't send email")
    parser.add_argument("--force-fail", help="Force a specific URL to be marked broken (for testing)")
    args = parser.parse_args()

    print("=" * 60)
    print("  TRUEFAM Website Health Monitor")
    print("=" * 60)
    log_run("Run started.")

    results = []
    for site in SITES:
        print(f"\n  Checking {site['name']} ({site['url']})...")
        r = check_site(site)
        # Test hook
        if args.force_fail and args.force_fail in site["url"]:
            r["healthy"] = False
            r["issue"] = "(FORCED for testing) " + (r["issue"] or "Simulated failure")
            r["suggestion"] = r["suggestion"] or "This is a test — no real fix needed."
        results.append(r)
        status = "HEALTHY ✓" if r["healthy"] else "BROKEN ✗"
        print(f"  → {status}")
        if not r["healthy"]:
            print(f"    Issue: {r['issue']}")
            print(f"    Fix:   {r['suggestion']}")

    log_run(f"Checked {len(results)} sites: {sum(1 for r in results if r['healthy'])} healthy, {sum(1 for r in results if not r['healthy'])} broken")

    subject, html = render_html_report(results)
    text = render_text_report(results)

    if args.no_email:
        print("\n" + "=" * 60)
        print("  --no-email: skipping email send")
        print("=" * 60)
        print(f"\nSubject: {subject}\n")
        print(text)
        return

    print(f"\n  Sending report to {RECIPIENT}...")
    try:
        send_email(subject, html, text)
        print("  ✓ Email sent successfully")
        log_run(f"Email sent to {RECIPIENT}: {subject}")
    except Exception as e:
        msg = f"Failed to send email: {e}"
        print(f"  ✗ {msg}")
        log_run(f"ERROR: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()

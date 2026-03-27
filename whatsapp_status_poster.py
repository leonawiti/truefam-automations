"""
TRUEFAM WhatsApp Status Auto-Poster

Reads the next unsent post from a Google Sheet, downloads the image
from Google Drive, and posts it as a WhatsApp Status update via
WhatsApp Web (Selenium).

Usage:
    python whatsapp_status_poster.py          # Post next unsent row
    python whatsapp_status_poster.py --dry    # Preview without posting
    python whatsapp_status_poster.py --login  # Open WhatsApp Web to scan QR code
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent / ".env")
# Also try the backend .env for shared credentials
load_dotenv(Path(__file__).resolve().parent.parent / "truefam-welfare-backend" / ".env")

SHEET_ID = os.getenv("WHATSAPP_SHEET_ID", "1hxuFZ7Ae0RGe0TCKMuGIu9wGjcmiB2e5kRXpzIerCyA")
CREDENTIALS_FILE = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path(__file__).resolve().parent.parent / "reports" / "ga4-service-account.json"),
)
CHROME_PROFILE = os.getenv(
    "CHROME_PROFILE_DIR",
    str(Path(__file__).resolve().parent / "chrome_profile"),
)

# Column indices (0-based)
COL_CAPTION = 0    # A
COL_IMAGE = 1      # B (unused)
COL_IMAGE_URL = 2  # C
COL_SENT = 3       # D


# ══════════════════════════════════════════════════════════════════════════════
# Google Sheets helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_sheet():
    """Authenticate and return the first worksheet of the content sheet."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SHEET_ID)
    return spreadsheet.sheet1


def find_next_unsent(sheet) -> dict | None:
    """Return the first row where the Sent column is blank."""
    rows = sheet.get_all_values()
    if not rows:
        return None

    # Skip header row
    for i, row in enumerate(rows[1:], start=2):  # row 2 in sheet (1-indexed)
        # Pad row to ensure we have enough columns
        while len(row) < 4:
            row.append("")

        sent = row[COL_SENT].strip().lower()
        if sent not in ("yes", "y", "true", "1"):
            caption = row[COL_CAPTION].strip()
            image_url = row[COL_IMAGE_URL].strip()
            if caption and image_url:
                return {
                    "row_number": i,
                    "caption": caption,
                    "image_url": image_url,
                }
    return None


def mark_as_sent(sheet, row_number: int):
    """Update the Sent column to 'Yes' for the given row."""
    sheet.update_cell(row_number, COL_SENT + 1, "Yes")  # gspread is 1-indexed
    print(f"  [Sheet] Row {row_number} marked as Sent=Yes")


# ══════════════════════════════════════════════════════════════════════════════
# Google Drive image download
# ══════════════════════════════════════════════════════════════════════════════

def extract_drive_file_id(url_or_id: str) -> str:
    """Extract a Google Drive file ID from a URL or raw ID."""
    # Full URL format: https://drive.google.com/file/d/{ID}/view...
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)

    # Already a raw file ID (no slashes, no http)
    if re.match(r"^[a-zA-Z0-9_-]+$", url_or_id):
        return url_or_id

    raise ValueError(f"Cannot extract Drive file ID from: {url_or_id}")


def download_image(url_or_id: str, dest_dir: str) -> str:
    """Download image from Google Drive and return local file path."""
    import requests

    file_id = extract_drive_file_id(url_or_id)
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"

    print(f"  [Drive] Downloading file ID: {file_id}")

    session = requests.Session()
    resp = session.get(download_url, stream=True, allow_redirects=True)

    # Handle Google Drive virus scan warning for large files
    for key, value in resp.cookies.items():
        if key.startswith("download_warning"):
            download_url = f"{download_url}&confirm={value}"
            resp = session.get(download_url, stream=True, allow_redirects=True)
            break

    resp.raise_for_status()

    # Determine extension from content-type
    content_type = resp.headers.get("Content-Type", "image/jpeg")
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    elif "gif" in content_type:
        ext = ".gif"

    filepath = os.path.join(dest_dir, f"status_image{ext}")
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    size_kb = os.path.getsize(filepath) / 1024
    print(f"  [Drive] Downloaded: {filepath} ({size_kb:.0f} KB)")
    return filepath


# ══════════════════════════════════════════════════════════════════════════════
# WhatsApp Web automation (Selenium)
# ══════════════════════════════════════════════════════════════════════════════

def get_driver():
    """Create a Selenium Chrome WebDriver with persistent profile."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    os.makedirs(CHROME_PROFILE, exist_ok=True)

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={os.path.abspath(CHROME_PROFILE)}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def wait_for_whatsapp_load(driver, timeout=60):
    """Wait for WhatsApp Web to fully load (past QR code if already logged in)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    print("  [WhatsApp] Waiting for WhatsApp Web to load...")
    driver.get("https://web.whatsapp.com/")

    # Wait for either the main app icons or QR code to appear
    WebDriverWait(driver, timeout).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, '[data-icon="status-refreshed"]')
        or d.find_elements(By.CSS_SELECTOR, '[data-icon="chat-filled-refreshed"]')
        or d.find_elements(By.CSS_SELECTOR, 'canvas[aria-label*="QR"]')
        or d.find_elements(By.CSS_SELECTOR, '[data-ref]')
    )

    # Check if QR code is showing (not logged in)
    qr_elements = driver.find_elements(By.CSS_SELECTOR, 'canvas[aria-label*="QR"]') + \
                   driver.find_elements(By.CSS_SELECTOR, '[data-ref]')

    if qr_elements and not driver.find_elements(By.CSS_SELECTOR, '[data-icon="status-refreshed"]'):
        print("\n  ╔══════════════════════════════════════════════════╗")
        print("  ║  SCAN THE QR CODE on your phone to log in.      ║")
        print("  ║  Waiting up to 120 seconds...                    ║")
        print("  ╚══════════════════════════════════════════════════╝\n")

        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-icon="status-refreshed"]'))
        )

    print("  [WhatsApp] Logged in successfully")
    time.sleep(3)


def post_status(driver, image_path: str, caption: str):
    """Post an image + caption as WhatsApp Status."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # Step 1: Click on Status tab (icon: status-refreshed)
    print("  [WhatsApp] Navigating to Status tab...")
    status_btn = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-icon="status-refreshed"]'))
    )
    status_btn.click()
    time.sleep(3)

    # Step 2: Click "Add Status" button
    print("  [WhatsApp] Clicking Add Status...")
    add_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, '[aria-label="Add Status"]'))
    )
    add_btn.click()
    time.sleep(3)

    # Step 3: Click "Photos & videos" option
    print("  [WhatsApp] Selecting Photos & videos...")
    photos_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, '[aria-label="Photos & videos"]'))
    )
    photos_btn.click()
    time.sleep(3)

    # Step 4: Upload image via file input (the image-specific one)
    print("  [WhatsApp] Uploading image...")
    file_inputs = driver.find_elements(
        By.CSS_SELECTOR, 'input[type="file"][accept*="image"]'
    )
    if not file_inputs:
        file_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')

    if not file_inputs:
        raise RuntimeError("Could not find file upload input.")

    file_inputs[0].send_keys(os.path.abspath(image_path))
    print("  [WhatsApp] Image uploaded, waiting for editor...")
    time.sleep(5)

    # Step 5: Add caption in the "Add a caption" field
    print("  [WhatsApp] Adding caption...")
    caption_box = None

    # Try the specific "Add a caption" label first
    caption_elements = driver.find_elements(
        By.CSS_SELECTOR, '[aria-label="Add a caption"]'
    )
    if caption_elements:
        caption_box = caption_elements[0]
    else:
        # Fallback to contenteditable textbox
        editable = driver.find_elements(
            By.CSS_SELECTOR, 'div[contenteditable="true"][role="textbox"]'
        )
        if editable:
            caption_box = editable[0]

    if caption_box:
        caption_box.click()
        time.sleep(0.5)
        caption_box.send_keys(caption)
        time.sleep(1)
        print("  [WhatsApp] Caption added")
    else:
        print("  [WhatsApp] Warning: Could not find caption input, posting without caption")

    # Step 6: Click Send (icon: wds-ic-send-filled)
    print("  [WhatsApp] Sending status...")
    send_btn = None
    send_selectors = [
        '[data-icon="wds-ic-send-filled"]',
        '[aria-label="Send"]',
        '[data-icon="send"]',
    ]
    for selector in send_selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        if elements:
            send_btn = elements[0]
            break

    if not send_btn:
        raise RuntimeError("Could not find Send button. WhatsApp Web UI may have changed.")

    send_btn.click()
    time.sleep(5)

    print("  [WhatsApp] Status posted successfully!")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="TRUEFAM WhatsApp Status Auto-Poster")
    parser.add_argument("--dry", action="store_true", help="Preview next post without sending")
    parser.add_argument("--login", action="store_true", help="Open WhatsApp Web to scan QR code")
    args = parser.parse_args()

    print("=" * 56)
    print("  TRUEFAM WhatsApp Status Auto-Poster")
    print("=" * 56)

    # ── Login-only mode ──
    if args.login:
        print("\n[Mode] Login — opening WhatsApp Web for QR scan...\n")
        driver = get_driver()
        try:
            wait_for_whatsapp_load(driver, timeout=120)
            print("\n  Session saved. You can now run without --login.\n")
            input("  Press Enter to close browser...")
        finally:
            driver.quit()
        return

    # ── Read sheet ──
    print("\n[1/5] Reading Google Sheet...")
    sheet = get_sheet()
    post = find_next_unsent(sheet)

    if not post:
        print("  No unsent posts found. All rows have been posted.")
        return

    print(f"  Found unsent post at row {post['row_number']}:")
    print(f"  Caption: {post['caption'][:80]}{'...' if len(post['caption']) > 80 else ''}")
    print(f"  Image:   {post['image_url'][:60]}...")

    # ── Dry run ──
    if args.dry:
        print("\n[Dry run] Would post the above. Exiting without sending.")
        return

    # ── Download image ──
    print("\n[2/5] Downloading image from Google Drive...")
    with tempfile.TemporaryDirectory() as tmpdir:
        image_path = download_image(post["image_url"], tmpdir)

        # ── Launch browser ──
        print("\n[3/5] Launching WhatsApp Web...")
        driver = get_driver()

        try:
            wait_for_whatsapp_load(driver)

            # ── Post status ──
            print("\n[4/5] Posting status update...")
            post_status(driver, image_path, post["caption"])

            # ── Mark as sent ──
            print("\n[5/5] Updating Google Sheet...")
            mark_as_sent(sheet, post["row_number"])

            print("\n" + "=" * 56)
            print("  Done! Status posted and sheet updated.")
            print("=" * 56)

        except Exception as e:
            print(f"\n  ERROR: {e}")
            print("  Status was NOT posted. Sheet was NOT updated.")
            raise
        finally:
            time.sleep(2)
            driver.quit()


if __name__ == "__main__":
    main()

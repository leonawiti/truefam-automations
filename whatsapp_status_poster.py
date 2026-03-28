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
COL_SENT = 3       # D (legacy — no longer used for WhatsApp)
COL_WHATSAPP = 7   # H — WhatsApp posting status


# ══════════════════════════════════════════════════════════════════════════════
# Google Sheets helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_spreadsheet():
    """Authenticate and return the spreadsheet object."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def get_all_sheets():
    """Return a list of (sheet_name, worksheet) for all sheets in the spreadsheet."""
    spreadsheet = get_spreadsheet()
    worksheets = spreadsheet.worksheets()
    return [(ws.title, ws) for ws in worksheets]


def find_next_unsent(sheet) -> dict | None:
    """Return the first row where column H (WhatsApp) is blank."""
    rows = sheet.get_all_values()
    if not rows:
        return None

    # Skip header row
    for i, row in enumerate(rows[1:], start=2):  # row 2 in sheet (1-indexed)
        # Pad row to ensure we have enough columns
        while len(row) < COL_WHATSAPP + 1:
            row.append("")

        whatsapp_status = row[COL_WHATSAPP].strip().lower()
        if whatsapp_status not in ("yes", "y", "true", "1"):
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
    """Update column H (WhatsApp) to 'Yes' for the given row."""
    sheet.update_cell(row_number, COL_WHATSAPP + 1, "Yes")  # gspread is 1-indexed
    print(f"  [Sheet] Row {row_number} column H (WhatsApp) marked as Yes")


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
    """Download image from Google Drive using service account auth."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    import io

    file_id = extract_drive_file_id(url_or_id)
    print(f"  [Drive] Downloading file ID: {file_id}")

    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    service = build("drive", "v3", credentials=creds)

    # Get file metadata to determine mime type
    meta = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    mime = meta.get("mimeType", "image/jpeg")
    name = meta.get("name", "image")
    print(f"  [Drive] File: {name} ({mime})")

    ext = ".jpg"
    if "png" in mime:
        ext = ".png"
    elif "webp" in mime:
        ext = ".webp"
    elif "gif" in mime:
        ext = ".gif"

    # Download file content
    request = service.files().get_media(fileId=file_id)
    os.makedirs(dest_dir, exist_ok=True)
    filepath = os.path.join(dest_dir, f"status_image{ext}")

    with open(filepath, "wb") as f:
        downloader = MediaIoBaseDownload(io.FileIO(filepath, "wb"), request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

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

    # Step 0: Navigate back to chats first (reset state from any previous post)
    chat_btns = driver.find_elements(By.CSS_SELECTOR, '[data-icon="chat-refreshed"]')
    if not chat_btns:
        chat_btns = driver.find_elements(By.CSS_SELECTOR, '[data-icon="chat-filled-refreshed"]')
    if chat_btns:
        chat_btns[0].click()
        time.sleep(2)

    # Step 1: Click on Status tab (icon varies based on active/inactive state)
    print("  [WhatsApp] Navigating to Status tab...")
    status_btn = None
    for icon in ["status-refreshed", "status-filled-refreshed"]:
        elements = driver.find_elements(By.CSS_SELECTOR, f'[data-icon="{icon}"]')
        if elements:
            status_btn = elements[0]
            break
    if not status_btn:
        # Fallback: try the Status button by aria-label
        status_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[aria-label="Status"]'))
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
    time.sleep(8)

    # Step 5: Add caption in the "Add a caption" field
    print("  [WhatsApp] Adding caption...")
    caption_box = None

    # Try multiple approaches with retries
    for attempt in range(3):
        caption_elements = driver.find_elements(
            By.CSS_SELECTOR, '[aria-label="Add a caption"]'
        )
        if caption_elements:
            caption_box = caption_elements[0]
            break

        editable = driver.find_elements(
            By.CSS_SELECTOR, 'div[contenteditable="true"][role="textbox"]'
        )
        if editable:
            caption_box = editable[-1]
            break

        time.sleep(2)

    if caption_box:
        caption_box.click()
        time.sleep(0.5)
        caption_box.send_keys(caption)
        time.sleep(1)
        print("  [WhatsApp] Caption added")
    else:
        print("  [WhatsApp] Warning: Could not find caption input, posting without caption")

    # Step 6: Click Send — try multiple selectors with retries
    print("  [WhatsApp] Sending status...")
    send_btn = None
    send_selectors = [
        '[data-icon="wds-ic-send-filled"]',
        '[aria-label="Send"]',
        '[data-icon="send"]',
    ]

    for attempt in range(3):
        for selector in send_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                send_btn = elements[0]
                break
        if send_btn:
            break
        # Also scan all icons for anything with "send"
        all_icons = driver.find_elements(By.CSS_SELECTOR, '[data-icon]')
        for el in all_icons:
            icon_name = el.get_attribute('data-icon') or ''
            if 'send' in icon_name.lower():
                send_btn = el
                break
        if send_btn:
            break
        time.sleep(2)

    if not send_btn:
        raise RuntimeError("Could not find Send button. WhatsApp Web UI may have changed.")

    send_btn.click()
    time.sleep(6)

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

    # ── Read all sheets and find one unsent post per sheet ──
    print("\n[1/6] Reading Google Sheets...")
    sheets = get_all_sheets()
    print(f"  Found {len(sheets)} sheet(s): {', '.join(name for name, _ in sheets)}")

    posts_to_send = []
    for sheet_name, worksheet in sheets:
        post = find_next_unsent(worksheet)
        if post:
            post["sheet_name"] = sheet_name
            post["worksheet"] = worksheet
            posts_to_send.append(post)
            print(f"\n  [{sheet_name}] Row {post['row_number']}:")
            print(f"    Caption: {post['caption'][:70]}{'...' if len(post['caption']) > 70 else ''}")
            print(f"    Image:   {post['image_url'][:55]}...")
        else:
            print(f"\n  [{sheet_name}] No unsent posts — all done")

    if not posts_to_send:
        print("\n  No unsent posts in any sheet. All caught up!")
        return

    print(f"\n  Total posts to send this run: {len(posts_to_send)}")

    # ── Dry run ──
    if args.dry:
        print("\n[Dry run] Would post the above. Exiting without sending.")
        return

    # ── Download all images first (skip posts with bad images) ──
    print("\n[2/6] Downloading images from Google Drive...")
    tmpdir = tempfile.mkdtemp()
    valid_posts = []
    for i, post in enumerate(posts_to_send):
        try:
            img_path = download_image(post["image_url"], tmpdir + f"/{i}")
            post["image_path"] = img_path
            valid_posts.append(post)
        except Exception as e:
            print(f"  [Skip] [{post['sheet_name']}] Row {post['row_number']}: {e}")
            # Try finding the next valid unsent row from this sheet
            sheet_name = post["sheet_name"]
            worksheet = post["worksheet"]
            rows = worksheet.get_all_values()
            found_replacement = False
            for ri, row in enumerate(rows[post["row_number"]:], start=post["row_number"] + 1):
                while len(row) < COL_WHATSAPP + 1:
                    row.append("")
                if row[COL_WHATSAPP].strip().lower() not in ("yes", "y", "true", "1"):
                    caption = row[COL_CAPTION].strip()
                    image_url = row[COL_IMAGE_URL].strip()
                    if caption and image_url:
                        try:
                            img_path = download_image(image_url, tmpdir + f"/{i}")
                            replacement = {
                                "row_number": ri,
                                "caption": caption,
                                "image_url": image_url,
                                "sheet_name": sheet_name,
                                "worksheet": worksheet,
                                "image_path": img_path,
                            }
                            valid_posts.append(replacement)
                            print(f"  [Retry] [{sheet_name}] Using row {ri} instead")
                            found_replacement = True
                            break
                        except Exception:
                            continue
            if not found_replacement:
                print(f"  [Skip] [{sheet_name}] No valid images found, skipping this sheet")
    posts_to_send = valid_posts

    if not posts_to_send:
        print("\n  No valid posts to send after downloading images.")
        return

    # ── Launch browser once ──
    print("\n[3/6] Launching WhatsApp Web...")
    driver = get_driver()

    try:
        wait_for_whatsapp_load(driver)

        # ── Post each status ──
        for i, post in enumerate(posts_to_send, 1):
            print(f"\n[4/6] Posting status {i}/{len(posts_to_send)} [{post['sheet_name']}]...")
            post_status(driver, post["image_path"], post["caption"])

            # Wait between posts to avoid issues
            if i < len(posts_to_send):
                print("  Waiting 5 seconds before next post...")
                time.sleep(5)

        # ── Mark all as sent ──
        print(f"\n[5/6] Updating Google Sheets...")
        for post in posts_to_send:
            mark_as_sent(post["worksheet"], post["row_number"])
            print(f"  [{post['sheet_name']}] Row {post['row_number']} ✓")

        print("\n" + "=" * 56)
        print(f"  Done! {len(posts_to_send)} status(es) posted and sheets updated.")
        print("=" * 56)

    except Exception as e:
        print(f"\n  ERROR: {e}")
        print("  Some posts may not have been sent. Check sheets manually.")
        raise
    finally:
        time.sleep(2)
        driver.quit()

    # Clean up temp dir
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

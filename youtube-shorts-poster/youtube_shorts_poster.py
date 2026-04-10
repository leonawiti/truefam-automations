"""
YouTube Shorts → WhatsApp Status Poster

Searches YouTube for Shorts matching a query, downloads the next unposted
video, and publishes it as a WhatsApp Status update via WhatsApp Web.

Usage:
    python youtube_shorts_poster.py                          # Post next short
    python youtube_shorts_poster.py --dry                    # Preview without posting
    python youtube_shorts_poster.py --query "tech memes"     # Custom search
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_QUERY = "coding memes shorts"
MAX_DURATION = 60       # seconds — skip videos longer than this
MAX_RESULTS = 20        # number of search results to scan
HISTORY_FILE = Path(__file__).resolve().parent / "posted_shorts.json"
CHROME_PROFILE = os.getenv(
    "CHROME_PROFILE_DIR",
    str(Path(__file__).resolve().parent / "chrome_profile"),
)


# ══════════════════════════════════════════════════════════════════════════════
# History tracking
# ══════════════════════════════════════════════════════════════════════════════

def load_history() -> set:
    """Load set of already-posted video IDs."""
    if HISTORY_FILE.exists():
        data = json.loads(HISTORY_FILE.read_text())
        return set(data.get("posted", []))
    return set()


def save_to_history(video_id: str, title: str):
    """Add a video ID to the posted history."""
    if HISTORY_FILE.exists():
        data = json.loads(HISTORY_FILE.read_text())
    else:
        data = {"posted": [], "log": []}

    if video_id not in data["posted"]:
        data["posted"].append(video_id)
        data["log"].append({
            "id": video_id,
            "title": title,
            "posted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    HISTORY_FILE.write_text(json.dumps(data, indent=2))
    print(f"  [History] Saved: {video_id}")


# ══════════════════════════════════════════════════════════════════════════════
# YouTube search & download
# ══════════════════════════════════════════════════════════════════════════════

def _query_to_hashtag_url(query: str) -> str:
    """Convert a search query to a YouTube hashtag Shorts URL."""
    # "coding memes shorts" -> "codingmemes"
    tag = query.lower().replace("shorts", "").replace(" ", "").strip()
    return f"https://www.youtube.com/hashtag/{tag}/shorts"


def search_shorts(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """Search YouTube for Shorts and return metadata list."""
    import yt_dlp

    hashtag_url = _query_to_hashtag_url(query)
    print(f"  [YouTube] Searching: {hashtag_url}")

    # First pass: get list of Shorts URLs via flat extraction (fast)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": max_results,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(hashtag_url, download=False)

    entries = result.get("entries", [])

    shorts = []
    for entry in entries:
        if not entry:
            continue
        video_id = entry.get("id", "")
        title = entry.get("title") or "Untitled"
        url = entry.get("url") or f"https://www.youtube.com/shorts/{video_id}"

        # Only include actual Shorts URLs
        if "/shorts/" in url or entry.get("duration", 0) <= MAX_DURATION:
            shorts.append({
                "id": video_id,
                "title": title,
                "duration": entry.get("duration", 0),
                "url": url,
            })

        if len(shorts) >= max_results:
            break

    print(f"  [YouTube] Found {len(shorts)} Shorts from {len(entries)} results")
    return shorts


def find_next_unposted(shorts: list[dict], history: set) -> dict | None:
    """Return the first Short that hasn't been posted yet."""
    for short in shorts:
        if short["id"] not in history:
            return short
    return None


def download_short(video_url: str, dest_dir: str) -> str:
    """Download a YouTube Short as MP4 and return the file path."""
    import yt_dlp

    output_template = os.path.join(dest_dir, "short.%(ext)s")

    ydl_opts = {
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }

    print(f"  [YouTube] Downloading: {video_url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filepath = ydl.prepare_filename(info)

    # Ensure .mp4 extension
    if not filepath.endswith(".mp4"):
        mp4_path = filepath.rsplit(".", 1)[0] + ".mp4"
        if os.path.exists(mp4_path):
            filepath = mp4_path

    if not os.path.exists(filepath):
        # yt-dlp may have used a different extension
        for f in os.listdir(dest_dir):
            if f.startswith("short"):
                filepath = os.path.join(dest_dir, f)
                break

    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    duration = info.get("duration", 0)
    print(f"  [YouTube] Downloaded: {filepath} ({size_mb:.1f} MB, {duration}s)")

    # Trim to 30 seconds if longer (WhatsApp Status limit on some versions)
    if duration and duration > 30:
        trimmed = os.path.join(dest_dir, "short_trimmed.mp4")
        print(f"  [ffmpeg] Trimming to 30 seconds...")
        subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-t", "30", "-c", "copy", trimmed],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if os.path.exists(trimmed) and os.path.getsize(trimmed) > 0:
            filepath = trimmed

    return filepath


# ══════════════════════════════════════════════════════════════════════════════
# WhatsApp Web automation (reuses session from whatsapp_status_poster)
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
    return webdriver.Chrome(service=service, options=options)


def wait_for_whatsapp(driver, timeout=60):
    """Wait for WhatsApp Web to load."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    print("  [WhatsApp] Waiting for WhatsApp Web to load...")
    driver.get("https://web.whatsapp.com/")

    WebDriverWait(driver, timeout).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, '[data-icon="status-refreshed"]')
        or d.find_elements(By.CSS_SELECTOR, '[data-icon="chat-filled-refreshed"]')
        or d.find_elements(By.CSS_SELECTOR, 'canvas[aria-label*="QR"]')
    )

    qr = driver.find_elements(By.CSS_SELECTOR, 'canvas[aria-label*="QR"]')
    if qr and not driver.find_elements(By.CSS_SELECTOR, '[data-icon="status-refreshed"]'):
        print("\n  ╔══════════════════════════════════════════════════╗")
        print("  ║  SCAN THE QR CODE on your phone to log in.      ║")
        print("  ╚══════════════════════════════════════════════════╝\n")
        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-icon="status-refreshed"]'))
        )

    print("  [WhatsApp] Logged in")
    time.sleep(3)


def post_video_status(driver, video_path: str):
    """Post a video as WhatsApp Status."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # Reset to chats
    chat_btns = driver.find_elements(By.CSS_SELECTOR, '[data-icon="chat-refreshed"]')
    if not chat_btns:
        chat_btns = driver.find_elements(By.CSS_SELECTOR, '[data-icon="chat-filled-refreshed"]')
    if chat_btns:
        chat_btns[0].click()
        time.sleep(2)

    # Status tab
    print("  [WhatsApp] Navigating to Status tab...")
    status_btn = None
    for icon in ["status-refreshed", "status-filled-refreshed"]:
        elements = driver.find_elements(By.CSS_SELECTOR, f'[data-icon="{icon}"]')
        if elements:
            status_btn = elements[0]
            break
    if not status_btn:
        status_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[aria-label="Status"]'))
        )
    status_btn.click()
    time.sleep(3)

    # Add Status
    print("  [WhatsApp] Clicking Add Status...")
    add_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, '[aria-label="Add Status"]'))
    )
    add_btn.click()
    time.sleep(3)

    # Photos & videos
    print("  [WhatsApp] Selecting Photos & videos...")
    photos_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, '[aria-label="Photos & videos"]'))
    )
    photos_btn.click()
    time.sleep(3)

    # Upload video via file input
    print("  [WhatsApp] Uploading video...")
    file_inputs = driver.find_elements(
        By.CSS_SELECTOR, 'input[type="file"][accept*="image"]'
    )
    if not file_inputs:
        file_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')

    if not file_inputs:
        raise RuntimeError("Could not find file upload input.")

    file_inputs[0].send_keys(os.path.abspath(video_path))
    print("  [WhatsApp] Video uploaded, waiting for processing...")
    time.sleep(10)  # Videos take longer to process

    # Click Send
    print("  [WhatsApp] Sending status...")
    send_btn = None
    for attempt in range(5):
        for selector in ['[data-icon="wds-ic-send-filled"]', '[aria-label="Send"]', '[data-icon="send"]']:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                send_btn = elements[0]
                break
        if send_btn:
            break
        time.sleep(2)

    if not send_btn:
        raise RuntimeError("Could not find Send button.")

    send_btn.click()
    time.sleep(8)  # Wait for video upload to complete

    print("  [WhatsApp] Video status posted!")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="YouTube Shorts → WhatsApp Status Poster")
    parser.add_argument("--dry", action="store_true", help="Preview without posting")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="YouTube search query")
    args = parser.parse_args()

    print("=" * 56)
    print("  YouTube Shorts → WhatsApp Status Poster")
    print("=" * 56)

    # ── Search YouTube ──
    print(f"\n[1/4] Searching YouTube...")
    history = load_history()
    print(f"  [History] {len(history)} videos previously posted")

    shorts = search_shorts(args.query)
    short = find_next_unposted(shorts, history)

    if not short:
        print("  No new Shorts found. All search results have been posted.")
        print("  Try a different --query or wait for new uploads.")
        return

    print(f"\n  Next Short to post:")
    print(f"    Title:    {short['title']}")
    print(f"    Duration: {short['duration']}s")
    print(f"    URL:      {short['url']}")

    if args.dry:
        print("\n[Dry run] Would post the above. Exiting.")
        return

    # ── Download ──
    print(f"\n[2/4] Downloading video...")
    tmpdir = tempfile.mkdtemp()
    video_path = download_short(short["url"], tmpdir)

    # ── Post to WhatsApp ──
    print(f"\n[3/4] Posting to WhatsApp Status...")
    driver = get_driver()

    try:
        wait_for_whatsapp(driver)
        post_video_status(driver, video_path)

        # ── Save to history ──
        print(f"\n[4/4] Saving to history...")
        save_to_history(short["id"], short["title"])

        print("\n" + "=" * 56)
        print(f"  Done! Posted: {short['title'][:50]}")
        print("=" * 56)

    except Exception as e:
        print(f"\n  ERROR: {e}")
        raise
    finally:
        time.sleep(2)
        driver.quit()

    # Clean up
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

"""
CAP MKE LinkedIn Company Page Auto-Poster

Logs into LinkedIn (with persistent profile to avoid CAPTCHAs after first
login) and publishes a pre-defined caption + poster image to a company
page. Designed to be invoked daily by launchd; the script itself enforces
the "every other day" cadence and the April 24, 2026 stop date.

Usage:
    python post_to_linkedin.py            # Normal run (post if it's time)
    python post_to_linkedin.py --force    # Post even if not scheduled
    python post_to_linkedin.py --dry      # Walk through without clicking Post
    python post_to_linkedin.py --login    # Just open LinkedIn for first-time login
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv

# ── Paths and config ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")
COMPANY_ID = os.getenv("LINKEDIN_COMPANY_ID", "77995106")
CHROME_PROFILE_DIR = os.getenv(
    "LINKEDIN_CHROME_PROFILE_DIR",
    str(BASE_DIR / "linkedin_chrome_profile"),
)

POSTER_PATH = BASE_DIR / "assets" / "capmke_event_poster.jpeg"
STATE_FILE = BASE_DIR / "state.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Cadence and stop date
POST_INTERVAL_DAYS = 2          # "every other day"
EVENT_STOP_DATE = date(2026, 4, 24)  # don't post on or after this date

# ── Caption ──────────────────────────────────────────────────────────────────
CAPTION = """We are excited to invite you to this ONE OF A KIND upcoming event! Due to limited capacity and for logistical planning, please register as soon as possible.

👉 Click here to register and share with your network — LIMITED SPACE:
https://tinyurl.com/CAPMKE2026Q2EventReg

📅 Happening April 24th — we look forward to seeing you there!

Featuring two renowned speakers who will share powerful strategies for financial growth. This engaging session will focus on:

🏠 Building wealth through real estate
💳 Leveraging personal and business credit
📈 Creating cash flow and expanding investment opportunities

You'll gain practical insights on how to:
• Navigate your career for growth
• Use credit strategically to acquire real estate
• Structure deals for long-term success
• Position yourself to scale your investments

Whether you're just starting your wealth-building journey, looking to grow your career, or ready to grow your portfolio — this event is for you. 🔥

Spots are limited — register now to reserve your place and share with others!

#CAPMKE #CareerGrowth #WealthBuilding #RealEstate #Networking #Milwaukee"""


# ─────────────────────────────────────────────────────────────────────────────
# State tracking (every-other-day cadence)
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def should_post_today(force: bool = False) -> tuple[bool, str]:
    """Return (should_post, reason)."""
    today = date.today()

    if today >= EVENT_STOP_DATE:
        return False, f"Event date reached ({EVENT_STOP_DATE}); stopping all posts."

    if force:
        return True, "Forced run (--force)"

    state = load_state()
    last_posted_str = state.get("last_posted_date")
    if not last_posted_str:
        return True, "No previous post recorded; posting today."

    try:
        last_posted = date.fromisoformat(last_posted_str)
    except ValueError:
        return True, "Could not parse last_posted_date; posting today."

    days_since = (today - last_posted).days
    if days_since >= POST_INTERVAL_DAYS:
        return True, f"{days_since} day(s) since last post (>= {POST_INTERVAL_DAYS})."
    return False, f"Only {days_since} day(s) since last post (need >= {POST_INTERVAL_DAYS})."


# ─────────────────────────────────────────────────────────────────────────────
# Selenium browser setup
# ─────────────────────────────────────────────────────────────────────────────

def get_driver():
    """Create a Selenium Chrome WebDriver with a persistent profile."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={os.path.abspath(CHROME_PROFILE_DIR)}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # Mask navigator.webdriver
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn login & posting
# ─────────────────────────────────────────────────────────────────────────────

def is_logged_in(driver) -> bool:
    from selenium.webdriver.common.by import By
    driver.get("https://www.linkedin.com/feed/")
    time.sleep(4)
    return "login" not in driver.current_url and "checkpoint" not in driver.current_url


def linkedin_login(driver) -> None:
    """Log into LinkedIn if not already logged in."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    if is_logged_in(driver):
        print("  [LinkedIn] Already logged in (persistent session).")
        return

    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        raise RuntimeError(
            "LinkedIn not logged in and LINKEDIN_EMAIL / LINKEDIN_PASSWORD "
            "are missing from .env"
        )

    print("  [LinkedIn] Logging in...")
    driver.get("https://www.linkedin.com/login")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "username"))
    )
    driver.find_element(By.ID, "username").send_keys(LINKEDIN_EMAIL)
    driver.find_element(By.ID, "password").send_keys(LINKEDIN_PASSWORD)
    driver.find_element(By.ID, "password").send_keys(Keys.RETURN)
    time.sleep(8)

    if "checkpoint" in driver.current_url or "challenge" in driver.current_url:
        print("\n  ╔══════════════════════════════════════════════════╗")
        print("  ║  LinkedIn is asking for verification (CAPTCHA    ║")
        print("  ║  or 2FA). Complete it in the browser window.     ║")
        print("  ║  Waiting up to 180 seconds...                    ║")
        print("  ╚══════════════════════════════════════════════════╝\n")
        WebDriverWait(driver, 180).until(
            lambda d: "feed" in d.current_url or "/in/" in d.current_url
        )

    if not is_logged_in(driver):
        raise RuntimeError("LinkedIn login failed.")
    print("  [LinkedIn] Login successful.")


def post_to_company_page(driver, image_path: Path, caption: str, dry: bool = False) -> None:
    """Navigate to the company admin page and publish a post with image."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    admin_url = f"https://www.linkedin.com/company/{COMPANY_ID}/admin/page-posts/published/"
    print(f"  [LinkedIn] Opening admin page: {admin_url}")
    driver.get(admin_url)
    time.sleep(6)

    # If LinkedIn redirected to /admin/ instead, that works too.
    if "admin" not in driver.current_url:
        driver.get(f"https://www.linkedin.com/company/{COMPANY_ID}/admin/")
        time.sleep(5)

    # Step 1: The admin page exposes "Video | Photo | Write article" quick
    # actions directly under the share box. Click "Photo" to open the photo
    # composer with the file picker in one step (skips the share modal).
    print("  [LinkedIn] Waiting for admin page to load...")
    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script("""
            var els = document.querySelectorAll('button, a, span, div');
            for (var i = 0; i < els.length; i++) {
                if ((els[i].innerText || '').trim() === 'Photo') return true;
            }
            return false;
        """)
    )
    time.sleep(2)

    print("  [LinkedIn] Looking for 'Photo' quick-action button...")
    photo_candidates = driver.execute_script("""
        var out = [];
        var els = document.querySelectorAll('button, a');
        els.forEach(function(el, i) {
            var text = (el.innerText || '').trim();
            var aria = (el.getAttribute('aria-label') || '').toLowerCase();
            if (text === 'Photo' || aria === 'photo' || aria === 'add a photo') {
                var r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    out.push({
                        idx: i,
                        tag: el.tagName,
                        text: text,
                        aria: aria,
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top + r.height/2),
                        w: Math.round(r.width),
                        h: Math.round(r.height)
                    });
                }
            }
        });
        return out;
    """)

    if not photo_candidates:
        raise RuntimeError("Could not find a 'Photo' quick-action button on admin page.")
    print(f"  [LinkedIn] Found {len(photo_candidates)} 'Photo' button(s)")

    # Pick the largest visible Photo button (likely the main share-box action)
    best = max(photo_candidates, key=lambda c: c["w"] * c["h"])
    print(f"  [LinkedIn] Clicking 'Photo' at ({best['x']}, {best['y']})...")
    for kind in ("mousePressed", "mouseReleased"):
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": kind,
            "x": best["x"],
            "y": best["y"],
            "button": "left",
            "clickCount": 1,
        })
    time.sleep(3)

    # Wait for the media-upload dialog (with its own file input) to appear.
    print("  [LinkedIn] Waiting for media upload dialog...")
    image_input = None
    for attempt in range(15):
        time.sleep(1)
        inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        for inp in inputs:
            accept = (inp.get_attribute("accept") or "").lower()
            if "image" in accept:
                image_input = inp
                break
        if image_input:
            print(f"  [LinkedIn] Found image input after {attempt + 1}s")
            break

    if not image_input:
        all_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        print(f"  [LinkedIn] No image-accept input found. All file inputs: {len(all_inputs)}")
        for i, inp in enumerate(all_inputs):
            print(f"    [{i}] accept={inp.get_attribute('accept')!r} name={inp.get_attribute('name')!r}")
        if all_inputs:
            image_input = all_inputs[0]
        else:
            raise RuntimeError("No file input element found anywhere on the page.")

    image_input.send_keys(str(image_path.resolve()))
    print("  [LinkedIn] Image uploaded, waiting for editor...")
    time.sleep(8)

    # Step 3: Click "Next" button (after the image preview/edit screen)
    try:
        next_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Next']]"))
        )
        next_btn.click()
        time.sleep(3)
    except Exception:
        print("  [LinkedIn] No 'Next' button — already on caption screen.")

    # Step 4: Type caption into the editor
    print("  [LinkedIn] Typing caption...")
    editor_selectors = [
        "//div[@role='textbox' and @contenteditable='true']",
        "//div[contains(@class,'ql-editor') and @contenteditable='true']",
    ]
    editor = None
    for sel in editor_selectors:
        try:
            editor = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.XPATH, sel))
            )
            break
        except Exception:
            continue
    if not editor:
        raise RuntimeError("Could not find caption editor.")
    editor.click()
    time.sleep(1)

    # ChromeDriver's send_keys doesn't support non-BMP characters (emojis),
    # so we use CDP Input.insertText which handles full unicode. We split
    # on newlines and insert each line + a newline character.
    from selenium.webdriver.common.keys import Keys
    for i, line in enumerate(caption.split("\n")):
        if line:
            driver.execute_cdp_cmd("Input.insertText", {"text": line})
        if i < len(caption.split("\n")) - 1:
            # Use Shift+Enter for soft newline within the editor
            editor.send_keys(Keys.SHIFT, Keys.ENTER)
        time.sleep(0.05)
    time.sleep(2)

    # Step 5: Click "Post" button
    if dry:
        print("  [DRY RUN] Would click Post now. Skipping.")
        time.sleep(3)
        return

    print("  [LinkedIn] Clicking Post...")
    post_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable(
            (By.XPATH, "//button[.//span[text()='Post'] and not(@disabled)]")
        )
    )
    post_btn.click()
    time.sleep(8)
    print("  [LinkedIn] Post published successfully ✓")


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def log_run(message: str) -> None:
    log_file = LOG_DIR / f"{date.today().isoformat()}.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"
    log_file.write_text(log_file.read_text() + line if log_file.exists() else line)
    print(line, end="")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Post even if not scheduled today")
    parser.add_argument("--dry", action="store_true", help="Walk through but don't actually post")
    parser.add_argument("--login", action="store_true", help="Open LinkedIn for first-time login")
    args = parser.parse_args()

    print("=" * 60)
    print("  CAP MKE LinkedIn Auto-Poster")
    print("=" * 60)
    log_run("Run started.")

    # ── Login-only mode ──
    if args.login:
        driver = get_driver()
        try:
            linkedin_login(driver)
            print("\n  Login complete. Session saved to:")
            print(f"    {CHROME_PROFILE_DIR}")
            print("  Press Ctrl+C in 10s if you want to stay logged in...")
            time.sleep(10)
        finally:
            driver.quit()
        return

    # ── Cadence check ──
    should_post, reason = should_post_today(force=args.force)
    print(f"\n  Cadence check: {reason}")
    log_run(f"Cadence check: {reason}")
    if not should_post:
        print("  Nothing to do. Exiting.")
        return

    if not POSTER_PATH.exists():
        msg = f"ERROR: Poster image not found at {POSTER_PATH}"
        print(f"  {msg}")
        log_run(msg)
        sys.exit(1)

    # ── Launch browser & post ──
    print(f"\n  Launching Chrome (profile: {CHROME_PROFILE_DIR})...")
    driver = get_driver()
    try:
        linkedin_login(driver)
        post_to_company_page(driver, POSTER_PATH, CAPTION, dry=args.dry)

        if not args.dry:
            state = load_state()
            state["last_posted_date"] = date.today().isoformat()
            state["last_post_caption_preview"] = CAPTION[:80]
            save_state(state)
            log_run("Post published successfully.")
            print("\n  Done. State updated.")
        else:
            log_run("Dry run completed (no post made).")

    except Exception as e:
        msg = f"ERROR during post: {e}"
        print(f"\n  {msg}")
        log_run(msg)
        # Save a screenshot for debugging
        try:
            screenshot_path = LOG_DIR / f"error_{int(time.time())}.png"
            driver.save_screenshot(str(screenshot_path))
            print(f"  Screenshot saved: {screenshot_path}")
        except Exception:
            pass
        sys.exit(1)
    finally:
        time.sleep(2)
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()

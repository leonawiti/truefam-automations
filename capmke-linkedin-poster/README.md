# CAP MKE LinkedIn Auto-Poster

Standalone automation that posts the CAP MKE Career Growth event flyer to a
LinkedIn company page **every other day at 8 AM Central**, until the event
date (April 24, 2026).

## What it does

1. Opens Chrome with a persistent profile (so login persists after first sign-in)
2. Logs into LinkedIn using credentials from `.env`
3. Navigates to the company admin page
4. Clicks "Start a post", uploads `assets/capmke_event_poster.jpeg`, types the caption
5. Publishes the post
6. Records the date in `state.json`

A daily launchd job invokes the script at 8 AM Central. The script itself
checks `state.json` and skips if it already posted within the last 2 days,
so the cadence is "every other day" even though launchd fires daily.
After **2026-04-24**, the script auto-disables.

## First-time setup

```bash
cd /Users/leonawiti/Documents/GitHub/Truefam/capmke-linkedin-poster

# 1. Install dependencies into a venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. First-time login (interactive — solves any CAPTCHA / verification)
python post_to_linkedin.py --login

# 3. Test a dry run (walks through everything except clicking Post)
python post_to_linkedin.py --dry --force

# 4. Test a real post
python post_to_linkedin.py --force
```

## Install the daily schedule

```bash
# Copy the plist into LaunchAgents
cp com.capmke.linkedinposter.plist ~/Library/LaunchAgents/

# Load it
launchctl load ~/Library/LaunchAgents/com.capmke.linkedinposter.plist

# Verify it's scheduled
launchctl list | grep capmke
```

To **stop** the schedule:
```bash
launchctl unload ~/Library/LaunchAgents/com.capmke.linkedinposter.plist
rm ~/Library/LaunchAgents/com.capmke.linkedinposter.plist
```

## Manual commands

```bash
./run.sh                 # Normal scheduled run
./run.sh --force         # Post now, ignoring the cadence check
./run.sh --dry --force   # Walk through without actually clicking Post
./run.sh --login         # Re-authenticate
```

## Files

| File | Purpose |
|------|---------|
| `post_to_linkedin.py` | Main script |
| `run.sh` | launchd entry point — activates venv and runs the script |
| `com.capmke.linkedinposter.plist` | launchd schedule (8 AM daily) |
| `assets/capmke_event_poster.jpeg` | Event flyer uploaded to LinkedIn |
| `.env` | Credentials (gitignored) |
| `state.json` | Tracks last post date for cadence check |
| `logs/` | Per-day run logs and launchd stdout/stderr |
| `linkedin_chrome_profile/` | Persistent Chrome profile (gitignored) |

## ⚠️ Important notes

- **LinkedIn ToS:** Browser automation violates LinkedIn's Terms of Service.
  Accounts using Selenium can be flagged or restricted. Use at your own risk.
  The persistent profile and `--user-agent` mask reduce detection risk but
  do not eliminate it.
- **First login:** LinkedIn will likely show a CAPTCHA or 2FA prompt the
  first time. Run `python post_to_linkedin.py --login` interactively and
  solve it manually. After that, the saved profile keeps you logged in.
- **Password security:** The password lives in `.env` (plaintext). The
  `.gitignore` excludes it, but the file itself is not encrypted. Consider
  rotating the password after the event ends.
- **Mac must be awake at 8 AM:** launchd cannot wake your Mac from sleep
  for `StartCalendarInterval` (only `LaunchDaemons` running as root can,
  via `pmset`). If your Mac is asleep, the job runs at the next wake.

# TRUEFAM Automations

Each automation lives in its own folder and is self-contained: own scripts,
state files, `.env`, `requirements.txt`, `run.sh` wrapper, and (where
applicable) launchd plist.

All folders share the repo-root virtualenv at `../.venv` by default. If you
want a truly isolated environment for any one of them, `cd` into that folder
and run `python -m venv .venv && pip install -r requirements.txt` — each
`run.sh` prefers a local `.venv` if present.

## Automations

| Folder | Description | Schedule |
|---|---|---|
| [capmke-linkedin-poster/](./capmke-linkedin-poster/) | Posts the CAP MKE event flyer to a LinkedIn company page (Selenium) | Daily 8:00 AM CT (every other day, stops 2026-04-24) |
| [whatsapp-status-poster/](./whatsapp-status-poster/) | Posts images+captions from a Google Sheet as WhatsApp Status (Selenium + gspread) | Daily 8:05 AM CT |
| [website-health-monitor/](./website-health-monitor/) | DNS/SSL/HTTP health checks with diagnosed-issue email reports | Daily 8:00 AM CT |
| [youtube-shorts-poster/](./youtube-shorts-poster/) | Downloads a YouTube Short and posts it as WhatsApp Status (yt-dlp + Selenium) | Manual |
| [linkedin-engagement-assistant/](./linkedin-engagement-assistant/) | Standalone engagement assistant (Playwright, Flask) | Manual |

## Installed launchd jobs

```bash
launchctl list | grep -E "capmke|whatsapp"
```

Each folder contains its own `com.*.plist`. To install or reload a schedule:

```bash
cp <folder>/com.*.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.*.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.*.plist
```

To stop a schedule:

```bash
launchctl unload ~/Library/LaunchAgents/com.*.plist
```

## Daily wake schedule (Apple Silicon Macs only wake from sleep, not power-off)

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 07:55:00   # wake 5 min before 8 AM
pmset -g sched                                      # verify
```

Leave the laptop plugged in and asleep (not shut down) overnight.

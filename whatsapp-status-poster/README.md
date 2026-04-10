# WhatsApp Status Auto-Poster

Reads the next unsent post from a Google Sheet, downloads the image from
Google Drive, and publishes it as a WhatsApp Status update via WhatsApp
Web (Selenium).

Scheduled daily at **8:05 AM Central** via launchd (`com.truefam.whatsappposter`).

## Layout

```
whatsapp-status-poster/
├── whatsapp_status_poster.py           # Main script
├── run.sh                              # launchd wrapper
├── com.truefam.whatsappposter.plist    # Daily schedule (8:05 AM CT)
├── .env                                # Credentials (gitignored)
├── .env.example
├── requirements.txt
├── chrome_profile/                     # Persistent WhatsApp Web session (gitignored)
├── logs/                               # Run logs
└── PRD_whatsapp_status_poster.md       # Product spec
```

## First-time setup

```bash
cd /Users/leonawiti/Documents/GitHub/Truefam/automations/whatsapp-status-poster

# Install dependencies (uses the shared repo venv by default)
../../.venv/bin/pip install -r requirements.txt

# Scan the WhatsApp Web QR code
./run.sh --login
```

## Manual commands

```bash
./run.sh                 # Post the next unsent row from the sheet
./run.sh --dry           # Preview without posting
./run.sh --login         # Re-scan the WhatsApp Web QR code
```

## Install / reinstall the schedule

```bash
cp com.truefam.whatsappposter.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.truefam.whatsappposter.plist
launchctl list | grep whatsapp
```

To stop:
```bash
launchctl unload ~/Library/LaunchAgents/com.truefam.whatsappposter.plist
```

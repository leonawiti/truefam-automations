# YouTube Shorts → WhatsApp Status Poster

Searches YouTube for Shorts matching a query, downloads the next unposted
video, and publishes it as a WhatsApp Status update via WhatsApp Web.

**Not currently scheduled** — run manually or add a launchd plist like the
other automations if you want recurring runs.

## Layout

```
youtube-shorts-poster/
├── youtube_shorts_poster.py        # Main script
├── run.sh                          # Wrapper (activates venv)
├── .env.example
├── requirements.txt
├── posted_shorts.json              # History of already-posted videos
├── chrome_profile/                 # Optional: own WhatsApp Web session
├── logs/
└── PRD_youtube_shorts_poster.md    # Product spec
```

## Tip: share the Chrome profile with whatsapp-status-poster

To avoid scanning the QR code twice, point this script at the same profile:

```bash
cp .env.example .env
# Then edit .env and uncomment CHROME_PROFILE_DIR=.../whatsapp-status-poster/chrome_profile
```

## Manual commands

```bash
./run.sh                              # Post the next unposted short
./run.sh --dry                        # Preview without posting
./run.sh --query "tech memes"         # Use a custom search query
```

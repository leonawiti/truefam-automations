# TRUEFAM Automations

## WhatsApp Status Auto-Poster

Reads marketing posts from a Google Sheet and publishes them as WhatsApp Status updates (image + caption) via WhatsApp Web.

### Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # Edit with your paths
```

**Share the Google Sheet** with your service account email:
`truefam-report@transcriber-387001.iam.gserviceaccount.com` (Viewer access)

### Usage

```bash
# First run — scan QR code to link WhatsApp Web
python whatsapp_status_poster.py --login

# Preview the next post without sending
python whatsapp_status_poster.py --dry

# Post the next unsent status
python whatsapp_status_poster.py
```

### How It Works

1. Reads the Google Sheet and finds the first row where **Sent = blank**
2. Downloads the image from Google Drive
3. Opens WhatsApp Web via Selenium (uses saved Chrome session)
4. Posts the image + caption as a Status update
5. Marks the row as **Sent = Yes** in the sheet

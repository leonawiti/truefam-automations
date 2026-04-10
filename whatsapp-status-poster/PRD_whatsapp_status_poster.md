# PRD: WhatsApp Status Auto-Poster

## 1. Executive Summary

An automated system that reads marketing posts from a Google Sheet and publishes them as WhatsApp Status updates (with image + caption) via WhatsApp Web. The automation uses Selenium to control a browser session on web.whatsapp.com, picks the next unsent post from the sheet, downloads the image from Google Drive, posts it as a status update, and marks the row as sent.

## 2. Problem Statement

TRUEFAM marketing posts are manually copy-pasted to WhatsApp Status — a repetitive, error-prone task that depends on a human remembering to do it. This automation eliminates that manual step and ensures consistent, timely posting.

## 3. Data Source

| Field | Description |
|---|---|
| **Google Sheet** | [Content Calendar](https://docs.google.com/spreadsheets/d/1hxuFZ7Ae0RGe0TCKMuGIu9wGjcmiB2e5kRXpzIerCyA/edit) |
| **Sheet ID** | `1hxuFZ7Ae0RGe0TCKMuGIu9wGjcmiB2e5kRXpzIerCyA` |

### Sheet Schema

| Column | Field | Type | Description |
|---|---|---|---|
| A | Caption | Text | Post caption / marketing copy |
| B | Image | (unused) | Legacy column |
| C | Image URL | URL / Google Drive file ID | Link to image on Google Drive |
| D | Sent | Yes/blank | Whether the post has been sent to WhatsApp |
| E | Facebook | Yes/blank | Facebook posting status |
| F | Linkedin | Yes/blank | LinkedIn posting status |
| G | Instagram | Yes/blank | Instagram posting status |

### Image URL Formats

The Image URL column contains two formats:
1. Full Drive URL: `https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing`
2. Raw file ID: `{FILE_ID}` (no URL wrapper)

Both must be handled. Images are downloaded via the Google Drive direct download URL:
`https://drive.google.com/uc?export=download&id={FILE_ID}`

## 4. Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Google Sheet    │────>│  Automation   │────>│  WhatsApp Web    │
│  (content feed)  │     │  (Python +    │     │  (Selenium        │
│                  │<────│   Selenium)   │     │   browser)       │
│  Mark as Sent    │     └──────────────┘     └──────────────────┘
└─────────────────┘           │
                              │ Download
                              v
                     ┌──────────────┐
                     │ Google Drive  │
                     │ (images)      │
                     └──────────────┘
```

## 5. Technical Approach

### 5.1 Google Sheets Access

- Use `gspread` library with service account credentials (same credentials as GA4 report)
- Read all rows, find first row where `Sent` column is blank
- After successful post, update `Sent` column to `Yes`

### 5.2 Image Download

- Parse Google Drive file ID from URL or raw ID
- Download via direct download URL to a temp file
- Support JPG, PNG, and other common image formats

### 5.3 WhatsApp Web Automation

- Use Selenium WebDriver with Chrome
- First run: user scans QR code to authenticate (session persists via Chrome profile)
- Subsequent runs: session is already authenticated
- Navigate to Status tab → click "My status" → attach image → add caption → send

### 5.4 Flow

1. Read Google Sheet
2. Find next unsent row (first row where column D is blank)
3. Download image from Google Drive
4. Open WhatsApp Web (Selenium)
5. Navigate to Status
6. Upload image + add caption
7. Post status
8. Mark row as Sent in Google Sheet
9. Clean up temp files

## 6. Prerequisites

| Requirement | Details |
|---|---|
| Python 3.10+ | Runtime |
| Chrome browser | Installed on machine |
| ChromeDriver | Matching Chrome version |
| Google service account | With Sheets API access (share sheet with service account email) |
| WhatsApp account | Linked to web.whatsapp.com |
| First-run QR scan | Manual one-time authentication |

## 7. Configuration

Environment variables (in `.env`):

```
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
WHATSAPP_SHEET_ID=1hxuFZ7Ae0RGe0TCKMuGIu9wGjcmiB2e5kRXpzIerCyA
CHROME_PROFILE_DIR=./chrome_profile
```

## 8. Dependencies

```
selenium
gspread
google-auth
requests
python-dotenv
```

## 9. Limitations & Risks

| Risk | Severity | Mitigation |
|---|---|---|
| WhatsApp Web session expires | MEDIUM | Chrome profile persists session; re-scan QR if needed |
| WhatsApp Web DOM changes | HIGH | Use resilient selectors; version-pin Selenium |
| Google Drive download blocked | LOW | Use direct download URL with file ID |
| Rate limiting by WhatsApp | MEDIUM | Post max 1 status per run; schedule runs apart |
| Image format unsupported | LOW | Convert to JPG before upload if needed |

## 10. Future Enhancements

- Schedule via cron (e.g., daily at 9 AM)
- Support video status posts
- Multi-platform posting (Facebook, LinkedIn, Instagram) from same sheet
- Posting queue with retry logic
- Dashboard showing posting history

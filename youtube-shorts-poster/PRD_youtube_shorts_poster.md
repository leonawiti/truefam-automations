# PRD: YouTube Shorts → WhatsApp Status Poster

## 1. Executive Summary

An automation that searches YouTube for short-form videos (Shorts) matching a given query (e.g., "coding memes shorts"), downloads the next unposted video, and publishes it as a WhatsApp Status update via WhatsApp Web. A local history file tracks which videos have already been posted to avoid duplicates.

## 2. Problem Statement

Posting engaging short-form video content to WhatsApp Status is a manual process — find a video, download it, open WhatsApp Web, upload, post. This automation reduces it to a single command.

## 3. Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│  YouTube     │────>│  Automation   │────>│  WhatsApp Web    │
│  Search API  │     │  (Python)     │     │  (Selenium)      │
│  "coding     │     │              │     │                  │
│   memes      │     │  Download    │     │  Post as Status  │
│   shorts"    │     │  via yt-dlp  │     │                  │
└──────────────┘     └──────────────┘     └──────────────────┘
                           │
                           v
                     ┌──────────────┐
                     │ posted.json  │
                     │ (history)    │
                     └──────────────┘
```

## 4. Technical Approach

### 4.1 YouTube Search

- Use the `yt-dlp` library to search YouTube for Shorts matching the query
- `yt-dlp` can search YouTube directly without an API key: `ytsearch20:coding memes shorts`
- Filter results to videos under 60 seconds (Shorts format)
- Return a list of video URLs + titles

### 4.2 History Tracking

- A local `posted_shorts.json` file stores video IDs that have been posted
- Before downloading, check if the video has already been posted
- After successful post, add the video ID to the history

### 4.3 Video Download

- Use `yt-dlp` to download the video in MP4 format
- Limit to 720p or lower to keep file size manageable
- WhatsApp Status supports videos up to 30 seconds (extended to ~3 min on some versions)
- Trim to 30 seconds if needed using ffmpeg

### 4.4 WhatsApp Web Posting

- Reuse the existing Selenium Chrome session from the WhatsApp Status poster
- Navigate to Status → Add Status → Photos & videos
- Upload the video file → send

## 5. Prerequisites

| Requirement | Details |
|---|---|
| Python 3.10+ | Runtime |
| yt-dlp | YouTube search + download |
| ffmpeg | Video trimming (if needed) |
| Chrome + Selenium | WhatsApp Web automation |
| WhatsApp session | Already linked via `whatsapp_status_poster.py --login` |

## 6. Configuration

| Setting | Default | Description |
|---|---|---|
| Search query | `coding memes shorts` | YouTube search term |
| Max duration | 60 seconds | Skip videos longer than this |
| Max results | 20 | Number of search results to scan |
| History file | `posted_shorts.json` | Tracks posted video IDs |

## 7. Usage

```bash
# Preview the next video without posting
python youtube_shorts_poster.py --dry

# Post one short to WhatsApp Status
python youtube_shorts_poster.py

# Use a custom search query
python youtube_shorts_poster.py --query "tech humor shorts"
```

## 8. Limitations & Risks

| Risk | Severity | Mitigation |
|---|---|---|
| YouTube search results change | LOW | History file prevents re-posting |
| Video too long for Status | MEDIUM | Auto-trim to 30s with ffmpeg |
| yt-dlp blocked by YouTube | LOW | Keep yt-dlp updated |
| WhatsApp video upload slow | MEDIUM | Wait longer for large files |
| Copyright concerns | MEDIUM | Shorts are public content; use for personal status only |

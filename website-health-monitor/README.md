# Website Health Monitor

Daily health checks for `truefamwel.com` and `epicbizintel.com` with a
status report emailed to `lawiti@truefamwel.com` at **8:00 AM Central**.

For each site, the monitor checks:
- DNS resolution
- SSL certificate validity (and expiry within 14 days)
- HTTP status code
- Response time (warns if > 5 seconds)
- Optional content sanity check

If anything is broken, the email includes the specific issue and a
suggested fix tailored to common failure modes (502, SSL expired, DNS,
connection refused, etc.).

## Layout

```
website-health-monitor/
├── health_check.py                      # Main script
├── run.sh                               # launchd wrapper
├── com.truefam.healthmonitor.plist      # Daily 8:00 AM CT schedule
├── .env                                 # Gmail credentials (gitignored)
├── .env.example
├── requirements.txt
├── .gitignore
├── README.md
└── logs/
```

## First-time setup

1. **Generate a Gmail App Password** at https://myaccount.google.com/apppasswords
   (2FA must be enabled on your Google account first)

2. **Fill in `.env`:**
   ```bash
   GMAIL_ADDRESS=your-email@gmail.com
   GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
   HEALTH_REPORT_RECIPIENT=lawiti@truefamwel.com
   ```

3. **Test it:**
   ```bash
   ./run.sh --no-email          # Print report to stdout
   ./run.sh                     # Send the actual email
   ./run.sh --force-fail truefamwel.com  # Test the broken-site path
   ```

4. **Install the schedule:**
   ```bash
   cp com.truefam.healthmonitor.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.truefam.healthmonitor.plist
   launchctl list | grep healthmonitor
   ```

## How issue diagnosis works

| Symptom | Diagnosis | Suggested fix |
|---|---|---|
| DNS lookup fails | Domain → IP resolution broken | Check registrar DNS records |
| SSL handshake fails | Cert invalid or wrong | Run `certbot renew` |
| HTTP 502 | Nginx can't reach upstream | Restart gunicorn/node/pm2 |
| HTTP 503 | Server overloaded | Check load, disable maintenance mode |
| HTTP 504 | Upstream timeout | Restart hung backend |
| HTTP 5xx | Server-side error | Check application logs |
| HTTP 404 | Route missing | Check Next.js / Django routing |
| HTTP 403 | Access denied | Check permissions, IP rules |
| Connection refused | Web server down | `systemctl start nginx` |
| Read timeout | App hung | Check process, restart |
| Slow (>5s) | Performance issue | Check load, queries, cache |
| SSL <14 days | Cert expiring soon | Renew the cert |

## Adding more sites

Edit `SITES` in [health_check.py](./health_check.py):

```python
SITES = [
    {"url": "https://truefamwel.com", "must_contain": None, "name": "TRUEFAM Welfare"},
    {"url": "https://epicbizintel.com", "must_contain": None, "name": "Epic Biz Intel"},
    # Add more here
]
```

Set `must_contain` to a string the page MUST contain (case-sensitive). If
the page returns 200 but doesn't include that string, the site is reported
as broken (useful for catching "site is up but serving wrong content").

## To stop the schedule

```bash
launchctl unload ~/Library/LaunchAgents/com.truefam.healthmonitor.plist
```

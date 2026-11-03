# Chernihivoblenergo → Google Calendar outage sync

Pulls power outages for **your street** from chernihivoblenergo.com.ua every 30 min
(Windows Task Scheduler) and keeps them in your Google Calendar — created, updated,
and removed when the schedule is cancelled.

## One-time setup (~15 min)

1. **Python 3.10+** on PATH (tick *Add to PATH* in the installer), then in this folder:
   `pip install -r requirements.txt`

2. **Find your street ID** and put it in `config.json` → `street_id`:
   1. Open https://chernihivoblenergo.com.ua/power_outages in Chrome/Edge.
   2. Press **F12** → **Network** tab.
   3. In the *Графіки обмеження* form pick your РЕМ, settlement and street, and run the search.
   4. Click the `list_acc` request → **Payload**. `req_param` contains `{"s":"<number>","t":...}` —
      that `<number>` is your `street_id`.
   5. Optional: set `house` (e.g. `"24к"`) to only sync outages that list your house.

3. **Google OAuth client** (free, personal use) at https://console.cloud.google.com:
   1. **Create a project** (project picker at the top → *New project*).
   2. **Enable the API:** search bar → *Google Calendar API* → **Enable**.
   3. **Configure Google Auth Platform:** search bar → *Google Auth Platform* → **Get started**:
      - *App information*: any app name (e.g. "Outage Sync"), your Gmail as support email
      - *Audience*: **External**
      - *Contact information*: your Gmail
      - Accept the policy → **Create**
   4. **Test user:** left menu → **Audience** → *Test users* → **+ Add users** → your Gmail → **Save**.
   5. **Client:** left menu → **Clients** → **+ Create client** → Application type **Desktop app** → **Create**.
      Download the JSON and save it in this folder as **`credentials.json`**.
   6. **Publish:** left menu → **Audience** → **Publish app** → Confirm.
      Without this, Google expires the refresh token after 7 days and the scheduled task stops working.

4. **First run (interactive)**: `python outage_sync.py`
   A browser opens asking you to approve calendar access. Google shows **"Google hasn't verified this app"** —
   expected for a personal app: **Advanced → Go to Outage Sync (unsafe) → Continue**.
   `token.json` is saved; later runs are silent.

5. **Schedule it**: `powershell -ExecutionPolicy Bypass -File .\register_task.ps1`
   (optional: `-EveryMinutes 15`)

> `credentials.json` and `token.json` are your personal secrets — never share them.

## Config (`config.json`)

| key | meaning |
|---|---|
| `street_id` | your street's ID on the site (step 2) |
| `house` | e.g. `"24к"` — only sync outages that list your house. Empty = all houses on the street |
| `types` | `6` restriction schedules (Графік обмеження), `1` planned, `2` emergency. Remove any you don't want |
| `calendar_id` | `primary`, or a dedicated calendar's ID (Calendar settings → *Integrate calendar*) |
| `reminder_minutes` | popup reminder before each outage |
| `attendees` | optional list of emails to add as guests, e.g. family: `["someone@example.com"]` |
| `send_updates` | `"none"` = guests see events silently; `"all"` = guests get an email for every new/changed/cancelled outage |

## Troubleshooting
- **`SSLError: CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`**: the site doesn't send its
  intermediate certificate. The script uses `truststore` (Windows certificate store) to work around it. If it still
  fails, open https://chernihivoblenergo.com.ua once in Edge/Chrome so Windows caches the certificate, then rerun.
- **`invalid_grant` / token expired**: the app wasn't published (step 3.6). Publish it, delete `token.json`, rerun step 4.
- **Guests don't see events**: their Google Calendar may only accept invites from known senders — add you as a
  contact, or set `send_updates` to `"all"` once.

## Behaviour
- No duplicates: each outage has a stable event ID; re-runs only update changes.
- Cancelled future outages are deleted from the calendar; past ones are left.
- If the site is down or returns garbage, the calendar is not touched.
- Log: `outage_sync.log`. Remove the task: `Unregister-ScheduledTask "Chernihivoblenergo Outage Sync"`.

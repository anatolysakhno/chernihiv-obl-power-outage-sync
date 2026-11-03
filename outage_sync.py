"""
Sync power outages from chernihivoblenergo.com.ua into Google Calendar.

Address: set "street_id" (and optionally "house") in config.json.

The site's "Графіки обмеження" form posts to /api/list_acc with
  {"req_param": urlencode(json({"s": <street_id>, "t": <type>}))}
where t = 6 (graphs / restriction schedules), 1 (planned), 2 (emergency).
Each row comes back as:
  [settlement, street, houses, start_iso, end_iso, ?, reason, ?]

Events are idempotent: each outage gets a deterministic event ID, so
re-running never creates duplicates. Future events that disappear from the
site (cancelled schedules) are removed from the calendar.
"""

from __future__ import annotations

__version__ = "1.0.0"

import argparse
import hashlib
import json
import logging
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests
import truststore
truststore.inject_into_ssl()   # use the Windows cert store (completes the site's missing intermediate cert)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------- config ---
BASE_DIR = Path(__file__).resolve().parent
CONFIG = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))

STREET_ID = CONFIG["street_id"]                  # your street ID on the site (see README)
TYPES = CONFIG.get("types", {"6": "Графік обмеження"})
HOUSE = (CONFIG.get("house") or "").strip()      # optional: only sync if your house is listed
CALENDAR_ID = CONFIG.get("calendar_id", "primary")
TZ = "Europe/Kyiv"
REMINDER_MIN = int(CONFIG.get("reminder_minutes", 30))
ATTENDEES = [e.strip().lower() for e in CONFIG.get("attendees", []) if e.strip()]
# "all" = email invites/updates/cancellations to guests, "none" = silent (event still shows in their Google Calendar)
SEND_UPDATES = CONFIG.get("send_updates", "none")
SOURCE_TAG = "chernihivoblenergo-outage-sync"

API_URL = "https://chernihivoblenergo.com.ua/api/list_acc"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

logging.basicConfig(
    filename=BASE_DIR / "outage_sync.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("outage_sync")


# ------------------------------------------------------------ site fetch ---
def fetch_outages(outage_type: str) -> list[dict]:
    req_param = urllib.parse.quote(json.dumps({"s": str(STREET_ID), "t": int(outage_type)}))
    r = requests.post(
        API_URL,
        json={"req_param": req_param},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://chernihivoblenergo.com.ua/power_outages",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if str(data.get("status")) != "200":
        raise RuntimeError(f"API returned status {data.get('status')}: {data}")

    out = []
    for row in data.get("list") or []:
        settlement, street, houses, start, end = row[0], row[1], row[2], row[3], row[4]
        reason = row[6] if len(row) > 6 else ""
        out.append({
            "type": outage_type,
            "settlement": settlement,
            "street": street,
            "houses": houses or "",
            "start": start,          # "2026-11-03T16:30:00" (local Kyiv time)
            "end": end,
            "reason": reason or "",
        })
    return out


def house_matches(houses: str) -> bool:
    if not HOUSE:
        return True
    listed = {h.strip().lower() for h in houses.split(",")}
    return HOUSE.lower() in listed


# -------------------------------------------------------- google calendar ---
def calendar_service():
    token_path = BASE_DIR / "token.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(BASE_DIR / "credentials.json"), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def event_id(o: dict) -> str:
    # Google event IDs: lowercase a-v and 0-9, 5-1024 chars. Hex digest qualifies.
    key = f"{STREET_ID}|{o['type']}|{o['start']}|{o['end']}"
    return "cho" + hashlib.sha1(key.encode("utf-8")).hexdigest()


def build_event(o: dict) -> dict:
    label = TYPES.get(o["type"], "Відключення")
    ev = {
        "id": event_id(o),
        "summary": f"⚡ {label}: {o['street']}",
        "location": f"{o['settlement']}, вул. {o['street']}",
        "description": (
            f"{o['reason']}\n\n"
            f"Будинки: {o['houses']}\n\n"
            f"Джерело: https://chernihivoblenergo.com.ua/power_outages"
        ),
        "start": {"dateTime": o["start"], "timeZone": TZ},
        "end": {"dateTime": o["end"], "timeZone": TZ},
        "status": "confirmed",
        "colorId": "11",  # red
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": REMINDER_MIN}]},
        "extendedProperties": {"private": {"source": SOURCE_TAG, "street_id": str(STREET_ID)}},
    }
    if ATTENDEES:
        ev["attendees"] = [{"email": e} for e in ATTENDEES]
        ev["guestsCanModify"] = False
        ev["guestsCanInviteOthers"] = False
    return ev


def attendee_set(ev: dict) -> set[str]:
    return {a["email"].lower() for a in ev.get("attendees", []) if not a.get("organizer")}


def upsert(svc, ev: dict) -> str:
    try:
        existing = svc.events().get(calendarId=CALENDAR_ID, eventId=ev["id"]).execute()
    except HttpError as e:
        if e.resp.status == 404:
            svc.events().insert(calendarId=CALENDAR_ID, body=ev, sendUpdates=SEND_UPDATES).execute()
            return "created"
        raise
    # Exists (possibly previously cancelled): update only if something changed.
    same_text = all(existing.get(f) == ev[f] for f in ("summary", "description", "location", "status"))
    same_time = all(existing.get(k, {}).get("dateTime", "")[:19] == ev[k]["dateTime"][:19]
                    for k in ("start", "end"))
    same_guests = attendee_set(existing) == attendee_set(ev)
    if same_text and same_time and same_guests:
        return "unchanged"
    svc.events().update(calendarId=CALENDAR_ID, eventId=ev["id"], body=ev,
                        sendUpdates=SEND_UPDATES).execute()
    return "updated"


def remove_vanished(svc, current_ids: set[str]) -> int:
    """Delete future events created by this script that the site no longer lists."""
    now = datetime.now(timezone.utc).isoformat()
    removed, page = 0, None
    while True:
        resp = svc.events().list(
            calendarId=CALENDAR_ID,
            privateExtendedProperty=[f"source={SOURCE_TAG}", f"street_id={STREET_ID}"],
            timeMin=now, singleEvents=True, pageToken=page, maxResults=250,
        ).execute()
        for ev in resp.get("items", []):
            if ev["id"] not in current_ids and ev.get("status") != "cancelled":
                svc.events().delete(calendarId=CALENDAR_ID, eventId=ev["id"],
                                    sendUpdates=SEND_UPDATES).execute()
                removed += 1
                log.info("removed %s %s", ev["id"], ev.get("start"))
        page = resp.get("nextPageToken")
        if not page:
            return removed


# ------------------------------------------------------------------ main ---
def main() -> int:
    log.info("outage_sync v%s", __version__)
    try:
        outages = []
        for t in TYPES:
            outages += [o for o in fetch_outages(t) if house_matches(o["houses"])]
    except Exception:
        # Site unreachable or changed: do NOT touch the calendar.
        log.exception("fetch failed; calendar left untouched")
        return 1

    svc = calendar_service()
    stats = {"created": 0, "updated": 0, "unchanged": 0}
    ids = set()
    for o in outages:
        ev = build_event(o)
        ids.add(ev["id"])
        stats[upsert(svc, ev)] += 1

    removed = remove_vanished(svc, ids)
    log.info("ok: %d outages | %s | removed %d", len(outages), stats, removed)
    print(f"{len(outages)} outages | {stats} | removed {removed}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync power outages into Google Calendar.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args()
    sys.exit(main())

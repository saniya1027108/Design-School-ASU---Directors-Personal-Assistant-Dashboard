"""
Google Calendar integration: OAuth2 and fetch events for monthly view.
Requires: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET in .env
Optional: GOOGLE_CALENDAR_ID (default "primary")
Token stored in data/google_calendar_token.json after first OAuth.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

# Paths
SRC_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SRC_DIR / "data"
TOKEN_PATH = DATA_DIR / "google_calendar_token.json"

# Scopes: calendar.events + Drive/Docs read (for agendas folder)
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]

# OAuth2 endpoints
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI_CALENDAR") or os.getenv("GOOGLE_REDIRECT_URI") or "http://127.0.0.1:5000/calendar/oauth2callback"


def get_client_config():
    """Return dict for OAuth2 with client_id and client_secret from env."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [GOOGLE_REDIRECT_URI],
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
        }
    }


def get_authorize_url(state=None):
    """Build Google OAuth2 authorization URL. state can be used for redirect after auth."""
    config = get_client_config()
    if not config:
        return None
    params = {
        "client_id": config["installed"]["client_id"],
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GOOGLE_AUTH_URI}?{q}"


def exchange_code_for_token(code):
    """Exchange authorization code for credentials and save to token file."""
    import requests
    config = get_client_config()
    if not config:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set")
    data = {
        "code": code,
        "client_id": config["installed"]["client_id"],
        "client_secret": config["installed"]["client_secret"],
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post(GOOGLE_TOKEN_URI, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    resp.raise_for_status()
    token_data = resp.json()
    # Store in same format as google-auth expects
    creds_dict = {
        "token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "token_uri": GOOGLE_TOKEN_URI,
        "client_id": config["installed"]["client_id"],
        "client_secret": config["installed"]["client_secret"],
        "scopes": SCOPES,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(creds_dict, f, indent=2)
    return True


def get_credentials():
    """Load credentials from token file. Refresh if expired. Returns None if not configured."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None
    if not TOKEN_PATH.exists():
        return None
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            creds_dict = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    creds = Credentials(
        token=creds_dict.get("token"),
        refresh_token=creds_dict.get("refresh_token"),
        token_uri=creds_dict.get("token_uri", GOOGLE_TOKEN_URI),
        client_id=creds_dict.get("client_id"),
        client_secret=creds_dict.get("client_secret"),
        scopes=creds_dict.get("scopes", SCOPES),
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes,
                }, f, indent=2)
        except Exception:
            return None
    return creds


def fetch_events(year, month, calendar_id=None):
    """
    Fetch events for the given month. Returns list of dicts:
    { "date": "YYYY-MM-DD", "title": str, "start": iso time, "end": iso time, "all_day": bool }
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        return []
    creds = get_credentials()
    if not creds:
        return []
    calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")
    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        # First and last day of month in UTC
        start_dt = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
        if month == 12:
            end_dt = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        else:
            end_dt = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events_raw = events_result.get("items", [])
    except (HttpError, OSError, Exception):
        return []

    out = []
    for ev in events_raw:
        summary = ev.get("summary") or "(No title)"
        start = ev.get("start", {})
        end = ev.get("end", {})
        start_str = start.get("dateTime") or start.get("date")
        end_str = end.get("dateTime") or end.get("date")
        all_day = "date" in start
        if start_str:
            if all_day:
                date_str = start_str  # YYYY-MM-DD
            else:
                try:
                    date_str = start_str[:10]
                except TypeError:
                    date_str = ""
            out.append({
                "id": ev.get("id"),
                "date": date_str,
                "title": summary,
                "start": start_str,
                "end": end_str,
                "all_day": all_day,
                "description": ev.get("description") or "",
                "html_link": ev.get("htmlLink"),
            })
    return out


def create_event(
    title,
    start_date,
    end_date=None,
    start_time=None,
    end_time=None,
    description=None,
    all_day=True,
    calendar_id=None,
):
    """
    Create a Google Calendar event.
    start_date / end_date: YYYY-MM-DD strings.
    start_time / end_time: optional "HH:MM" or "HH:MM:SS" (local time).
    If all_day=True, start_time/end_time are ignored.
    Returns created event dict or raises.
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        raise RuntimeError("Google API client not installed")
    creds = get_credentials()
    if not creds:
        raise RuntimeError("Google Calendar not connected")
    calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")
    end_date = end_date or start_date

    if all_day:
        body = {
            "summary": title,
            "description": description or "",
            "start": {"date": start_date, "timeZone": "UTC"},
            "end": {"date": end_date, "timeZone": "UTC"},
        }
    else:
        # Use local timezone; Google expects ISO format
        start_dt = f"{start_date}T{start_time or '09:00:00'}"
        end_dt = f"{end_date}T{end_time or '10:00:00'}"
        if len((start_time or "09:00").split(":")) == 2:
            start_dt += ":00"
        if len((end_time or "10:00").split(":")) == 2:
            end_dt += ":00"
        body = {
            "summary": title,
            "description": description or "",
            "start": {"dateTime": start_dt, "timeZone": "UTC"},
            "end": {"dateTime": end_dt, "timeZone": "UTC"},
        }

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    created = service.events().insert(calendarId=calendar_id, body=body).execute()
    return created


def is_connected():
    """True if we have valid token (or credentials configured and token file exists)."""
    return get_credentials() is not None


def is_configured():
    """True if client id/secret are set (so user can connect)."""
    return get_client_config() is not None

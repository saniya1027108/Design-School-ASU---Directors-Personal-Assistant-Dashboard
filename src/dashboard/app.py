from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime
import os
import json
import uuid
from pathlib import Path
import sys

# Ensure src is in sys.path for imports
SRC_DIR = Path(__file__).parent.parent  # <project>/src
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Use the dashboard's templates folder explicitly (do not fall back to top-level templates)
TEMPLATE_FOLDER = (SRC_DIR / "dashboard" / "templates").resolve()
if not TEMPLATE_FOLDER.exists():
    raise RuntimeError(f"Templates folder not found: {TEMPLATE_FOLDER}")

# Create Flask app with the explicit template folder
app = Flask(__name__, template_folder=str(TEMPLATE_FOLDER))
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")

# Data directory (inside src/data)
DATA_DIR = SRC_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EMAILS_JSON = DATA_DIR / "emails.json"
ACTION_ITEMS_JSON = DATA_DIR / "action_items.json"
DRAFTS_JSON = DATA_DIR / "drafts.json"
MEETINGS_JSON = DATA_DIR / "meetings.json"
BOARDS_JSON = DATA_DIR / "boards.json"
USER_SETTINGS_JSON = DATA_DIR / "user_settings.json"
DRIVE_AGENDAS_JSON = DATA_DIR / "drive_agendas.json"

def _flatten_action_items(data):
    if isinstance(data, dict):
        flat = []
        for v in data.values():
            if isinstance(v, list):
                flat.extend(v)
        return flat
    return data

def load_json(path):
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if p.resolve() == Path(ACTION_ITEMS_JSON).resolve():
        return _flatten_action_items(data)
    return data

def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# --- Import your pipeline sync/extract functions ---
from outlook.sync.sync_outlook_json import sync_emails_to_json
from agendas.extract_and_sync import extract_action_items_from_agendas
from outlook.sync.draft_replies import generate_draft_reply, lookup_sender_category
from outlook.sync.outlook_read import fetch_message
from outlook.sync.outlook_send import send_email
from outlook.sync.revise_drafts import process_revisions
from outlook.sync.send_approved_replies import send_approved_replies
from agendas.extract_and_sync import extract_action_items_from_notes_text
# Optional Google Docs helpers (service-account-backed helpers)
try:
    from agendas.extract_and_sync import (
        process_google_doc_to_action_items,
        process_google_docs_in_folder,
        list_docs_in_folder,
        fetch_google_doc_text,
    )
    HAS_GOOGLE_DOCS_INTEGRATION = True
except Exception:
    HAS_GOOGLE_DOCS_INTEGRATION = False

# Optional OAuth flow libs
try:
    from google_auth_oauthlib.flow import Flow
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from googleapiclient.discovery import build as g_build
    GOOGLE_OAUTH_AVAILABLE = True
except Exception:
    GOOGLE_OAUTH_AVAILABLE = False

# Google Calendar (optional)
try:
    from google_calendar.calendar_service import (
        get_authorize_url,
        exchange_code_for_token,
        fetch_events,
        create_event as calendar_create_event,
        is_connected as calendar_is_connected,
        is_configured as calendar_is_configured,
    )
    HAS_GOOGLE_CALENDAR = True
except ImportError:
    HAS_GOOGLE_CALENDAR = False

def load_meetings():
    data = load_json(MEETINGS_JSON) if Path(MEETINGS_JSON).exists() else []
    return data if isinstance(data, list) else []

def save_meetings(meetings):
    save_json(MEETINGS_JSON, meetings)


def load_drive_agendas():
    """Load last Drive import result for display on Agendas tab. Returns dict with by_folder, last_import, etc."""
    if not Path(DRIVE_AGENDAS_JSON).exists():
        return {}
    try:
        with open(DRIVE_AGENDAS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_drive_agendas(data):
    save_json(DRIVE_AGENDAS_JSON, data)


def load_boards():
    data = load_json(BOARDS_JSON)
    if isinstance(data, list):
        return data
    return data.get("boards", []) if isinstance(data, dict) else []

def save_boards(boards):
    save_json(BOARDS_JSON, boards)

def load_user_settings():
    if not USER_SETTINGS_JSON.exists():
        return {}
    with open(USER_SETTINGS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def save_user_settings(settings):
    USER_SETTINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_SETTINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

@app.context_processor
def inject_user_and_date():
    user_name = session.get("user_name") or load_user_settings().get("display_name", "Guest")
    today = datetime.now().strftime("%d/%m/%y")
    return dict(user_name=user_name, current_date=today)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return wrapped

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username:
            session["logged_in"] = True
            session["user_name"] = username
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        return render_template("login.html", error="Please enter a username.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    emails = load_json(EMAILS_JSON)
    action_items = load_json(ACTION_ITEMS_JSON)
    drafts = load_json(DRAFTS_JSON)
    calendar_connected = HAS_GOOGLE_CALENDAR and calendar_is_connected() if HAS_GOOGLE_CALENDAR else False
    calendar_configured = HAS_GOOGLE_CALENDAR and calendar_is_configured() if HAS_GOOGLE_CALENDAR else False
    return render_template(
        "dashboard.html",
        emails=emails,
        action_items=action_items,
        drafts=drafts,
        calendar_connected=calendar_connected,
        calendar_configured=calendar_configured,
    )

# --- Google Calendar ---
@app.route("/calendar/connect")
@login_required
def calendar_connect():
    if not HAS_GOOGLE_CALENDAR:
        return redirect(url_for("dashboard"))
    url = get_authorize_url(state=url_for("dashboard"))
    if not url:
        return redirect(url_for("dashboard"))
    return redirect(url)

@app.route("/calendar/oauth2callback")
@login_required
def calendar_oauth2callback():
    if not HAS_GOOGLE_CALENDAR:
        return redirect(url_for("dashboard"))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("dashboard"))
    try:
        exchange_code_for_token(code)
    except Exception:
        pass
    return redirect(url_for("dashboard"))


# ------------------
# Google Docs OAuth
# ------------------
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]

def _user_creds_path():
    uname = session.get("user_name") or "default"
    return DATA_DIR / f"google_creds_{uname}.json"

def _save_user_google_credentials(creds):
    p = _user_creds_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        j = creds.to_json()
    except Exception:
        # fallback: build dict
        j = json.dumps({
            "token": creds.token,
            "refresh_token": getattr(creds, "refresh_token", None),
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        })
    with open(p, "w", encoding="utf-8") as f:
        f.write(j)

def _load_user_google_credentials():
    p = _user_creds_path()
    if not p.exists():
        return None
    if not GOOGLE_OAUTH_AVAILABLE:
        return None
    info = json.load(open(p, "r", encoding="utf-8"))
    try:
        creds = OAuthCredentials.from_authorized_user_info(info, scopes=SCOPES)
        return creds
    except Exception:
        try:
            # sometimes stored as JSON string
            info2 = json.loads(info) if isinstance(info, str) else info
            creds = OAuthCredentials.from_authorized_user_info(info2, scopes=SCOPES)
            return creds
        except Exception:
            return None


@app.route("/google_docs/connect")
@login_required
def google_docs_connect():
    if not GOOGLE_OAUTH_AVAILABLE:
        return redirect(url_for("agendas_list"))
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return redirect(url_for("agendas_list"))
    # Use explicit env var if provided, otherwise build from request.url_root
    env_redirect = os.getenv("GOOGLE_REDIRECT_URI_DOCS") or os.getenv("GOOGLE_REDIRECT_URI")
    if env_redirect:
        redirect_uri = env_redirect.rstrip('/')
    else:
        redirect_uri = request.url_root.rstrip('/') + "/google_docs/oauth2callback"
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/google_docs/oauth2callback")
@login_required
def google_docs_oauth2callback():
    if not GOOGLE_OAUTH_AVAILABLE:
        return redirect(url_for("agendas_list"))
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    env_redirect = os.getenv("GOOGLE_REDIRECT_URI_DOCS") or os.getenv("GOOGLE_REDIRECT_URI")
    if env_redirect:
        redirect_uri = env_redirect.rstrip('/')
    else:
        redirect_uri = request.url_root.rstrip('/') + "/google_docs/oauth2callback"
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    state = session.get("google_oauth_state")
    flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state, redirect_uri=redirect_uri)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    _save_user_google_credentials(creds)
    return redirect(url_for("agendas_list"))

@app.route("/api/calendar/events")
@login_required
def api_calendar_events():
    if not HAS_GOOGLE_CALENDAR:
        return jsonify({"connected": False, "configured": False, "events": []})
    configured = calendar_is_configured()
    connected = calendar_is_connected()
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    now = datetime.now()
    if not year:
        year = now.year
    if not month:
        month = now.month
    events = fetch_events(year, month) if connected else []
    return jsonify({
        "connected": connected,
        "configured": configured,
        "events": events,
        "year": year,
        "month": month,
    })


@app.route("/api/calendar/events", methods=["POST"])
@login_required
def api_calendar_events_create():
    """Create a new calendar event. JSON: title, date (YYYY-MM-DD), start_time?, end_time?, description?, all_day? (default true)."""
    if not HAS_GOOGLE_CALENDAR:
        return jsonify({"success": False, "error": "Google Calendar not available"}), 400
    if not calendar_is_connected():
        return jsonify({"success": False, "error": "Google Calendar not connected"}), 401
    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "error": "Title is required"}), 400
    start_date = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    end_date = data.get("end_date") or start_date
    start_time = data.get("start_time") or None
    end_time = data.get("end_time") or None
    description = (data.get("description") or "").strip() or None
    all_day = data.get("all_day", True)
    try:
        created = calendar_create_event(
            title=title,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            description=description,
            all_day=all_day,
        )
        return jsonify({
            "success": True,
            "event_id": created.get("id"),
            "html_link": created.get("htmlLink"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# --- Google Drive Agendas (same OAuth as Calendar; folder structure = people/projects) ---
try:
    from google_calendar.drive_agendas import walk_agendas_and_extract
    HAS_DRIVE_AGENDAS = True
except Exception:
    HAS_DRIVE_AGENDAS = False


@app.route("/api/drive/agendas/extract", methods=["POST"])
@login_required
def api_drive_agendas_extract():
    """
    Extract action items from Google Docs in a Drive folder (and its subfolders).
    Body: { "folder_id": optional (else from env GOOGLE_DRIVE_AGENDAS_FOLDER_ID), "merge": optional bool }
    Returns by_folder structure; if merge=true, appends all items to action_items.json.
    """
    print("[Drive extract] POST /api/drive/agendas/extract received", flush=True)
    if not HAS_DRIVE_AGENDAS:
        return jsonify({"success": False, "error": "Drive agendas module not available"}), 500
    data = request.json or {}
    folder_id = data.get("folder_id") or os.getenv("GOOGLE_DRIVE_AGENDAS_FOLDER_ID")
    if not folder_id:
        return jsonify({"success": False, "error": "Folder ID required. Set GOOGLE_DRIVE_AGENDAS_FOLDER_ID in .env or pass folder_id in the request."}), 400
    print("[Drive extract] Starting walk_agendas_and_extract for folder_id=%s..." % (folder_id[:20] + "..." if len(folder_id) > 20 else folder_id), flush=True)
    result = walk_agendas_and_extract(root_folder_id=folder_id)
    print("[Drive extract] Done. subfolders=%s, docs_in_root=%s" % (result.get("stats", {}).get("subfolders_found"), result.get("stats", {}).get("docs_in_root")), flush=True)
    if result.get("error"):
        return jsonify({"success": False, "error": result["error"], "by_folder": result.get("by_folder", {})}), 400
    merge = data.get("merge", False)
    if merge and result.get("all_items_flat"):
        action_items = load_json(ACTION_ITEMS_JSON)
        action_items.extend(result["all_items_flat"])
        save_json(ACTION_ITEMS_JSON, action_items)
    # Persist so Agendas tab can show these docs and their action items
    save_drive_agendas({
        "last_import": datetime.now().isoformat(),
        "folder_id": folder_id,
        "by_folder": result.get("by_folder", {}),
        "stats": result.get("stats", {}),
    })
    return jsonify({
        "success": True,
        "by_folder": result.get("by_folder", {}),
        "all_items_flat": result.get("all_items_flat", []),
        "count": len(result.get("all_items_flat", [])),
        "stats": result.get("stats", {}),
    })


@app.route("/emails_full")
@login_required
def emails_full():
    emails = load_json(EMAILS_JSON)
    drafts = load_json(DRAFTS_JSON)

    # Build map of latest draft by email_id for quick lookup in template
    drafts_map = {}
    for d in drafts:
        # keep last occurrence as latest
        drafts_map[d.get("email_id")] = d

    return render_template("emails_full.html", emails=emails, drafts_map=drafts_map)

@app.route("/action_items_full")
@login_required
def action_items_full():
    action_items = load_json(ACTION_ITEMS_JSON)
    # Group by status for Kanban
    grouped = {"todo": [], "done": [], "pending": [], "other": []}
    for item in action_items:
        status = (item.get("status") or "todo").lower()
        if status in grouped:
            grouped[status].append(item)
        else:
            grouped["other"].append(item)
    return render_template("actio" \
    "n_items_full.html", grouped=grouped)

@app.route("/sync_emails", methods=["POST"])
@login_required
def sync_emails():
    sync_emails_to_json(EMAILS_JSON)
    return jsonify({"success": True})

@app.route("/extract_action_items", methods=["POST"])
@login_required
def extract_action_items():
    extract_action_items_from_agendas(ACTION_ITEMS_JSON)
    return jsonify({"success": True})

@app.route("/emails")
@login_required
def get_emails():
    return jsonify(load_json(EMAILS_JSON))

@app.route("/action_items")
@login_required
def get_action_items():
    return jsonify(load_json(ACTION_ITEMS_JSON))

@app.route("/drafts")
@login_required
def get_drafts():
    return jsonify(load_json(DRAFTS_JSON))

@app.route("/drafts/generate", methods=["POST"])
@login_required
def generate_draft():
    data = request.json
    email_id = data["email_id"]
    instruction = data.get("instruction", "")

    emails = load_json(EMAILS_JSON)
    email = next((e for e in emails if e.get("id") == email_id), None)
    if not email:
        return jsonify({"success": False, "error": "Email not found"}), 404

    # Update email workflow and draft_status
    email["reply_instruction"] = instruction
    email["workflow_status"] = "generating draft"
    email["draft_status"] = "Generate Draft"
    save_json(EMAILS_JSON, emails)

    try:
        # Fetch full message if needed
        message = fetch_message(email_id)
        original_body = message.get("body", {}).get("content", "")
        sender_email = message.get("from", {}).get("emailAddress", {}).get("address", "")
        sender_name = message.get("from", {}).get("emailAddress", {}).get("name", "") or sender_email

        # Generate draft reply (uses your OpenAI wrapper)
        draft_html = generate_draft_reply(instruction, original_body, sender_name, lookup_sender_category(sender_email))

        # Save draft to drafts.json
        drafts = load_json(DRAFTS_JSON)
        new_draft = {
            "email_id": email_id,
            "instruction": instruction,
            "draft": draft_html,
            "status": "Generated"
        }
        drafts.append(new_draft)
        save_json(DRAFTS_JSON, drafts)

        # Update email row
        email["draft_status"] = "Generate Draft"
        email["workflow_status"] = "generated"
        save_json(EMAILS_JSON, emails)

        return jsonify({"success": True, "draft": new_draft})
    except Exception as e:
        email["workflow_status"] = "error"
        save_json(EMAILS_JSON, emails)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/drafts/revise", methods=["POST"])
@login_required
def revise_draft():
    data = request.json
    email_id = data["email_id"]
    revision_notes = data.get("revision_notes", "")
    # fetch email
    emails = load_json(EMAILS_JSON)
    email = next((e for e in emails if e.get("id") == email_id), None)
    if not email:
        return jsonify({"success": False, "error": "Email not found"}), 404

    # update workflow
    email["workflow_status"] = "revising"
    email["draft_status"] = "Needs Revision"
    save_json(EMAILS_JSON, emails)

    try:
        # Fetch original message
        message = fetch_message(email_id)
        original_body = message.get("body", {}).get("content", "")
        sender_email = message.get("from", {}).get("emailAddress", {}).get("address", "")
        sender_name = message.get("from", {}).get("emailAddress", {}).get("name", "") or sender_email

        # Combine instruction + revision notes to request revised draft
        base_instruction = email.get("reply_instruction", "")
        combined_instruction = f"{base_instruction}\n\nRevision notes:\n{revision_notes}"

        revised_html = generate_draft_reply(combined_instruction, original_body, sender_name, lookup_sender_category(sender_email), revision_notes=revision_notes)

        # Save revised draft entry (append)
        drafts = load_json(DRAFTS_JSON)
        new_draft = {
            "email_id": email_id,
            "instruction": combined_instruction,
            "draft": revised_html,
            "revision_notes": revision_notes,
            "status": "Revised"
        }
        drafts.append(new_draft)
        save_json(DRAFTS_JSON, drafts)

        # update email row
        email["draft_status"] = "Needs Revision"
        email["workflow_status"] = "revised"
        save_json(EMAILS_JSON, emails)

        return jsonify({"success": True, "draft": new_draft})
    except Exception as e:
        email["workflow_status"] = "error"
        save_json(EMAILS_JSON, emails)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/drafts/send", methods=["POST"])
@login_required
def send_draft():
    data = request.json
    email_id = data["email_id"]

    drafts = load_json(DRAFTS_JSON)
    draft = next((d for d in drafts if d["email_id"] == email_id), None)
    if not draft:
        return jsonify({"success": False, "error": "Draft not found"}), 404

    # set workflow to sending
    emails = load_json(EMAILS_JSON)
    email = next((e for e in emails if e.get("id") == email_id), None)
    if email:
        email["workflow_status"] = "sending"
        save_json(EMAILS_JSON, emails)

    try:
        # Send via outlook
        send_email(
            to=email.get("email") if email else None,
            subject="Re: " + (email.get("subject") or ""),
            body=draft["draft"],
            message_id=email_id
        )
        # update draft status
        draft["status"] = "Sent"
        save_json(DRAFTS_JSON, drafts)

        # update email row
        if email:
            email["draft_status"] = "Sent"
            email["workflow_status"] = "complete"
            save_json(EMAILS_JSON, emails)

        return jsonify({"success": True})
    except Exception as e:
        if email:
            email["workflow_status"] = "error"
            save_json(EMAILS_JSON, emails)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/emails/update", methods=["POST"])
@login_required
def update_email_row():
    """Update a single email row (reply_instruction, draft_status, workflow_status)"""
    data = request.json
    email_id = data.get("id")
    if not email_id:
        return jsonify({"success": False, "error": "Missing id"}), 400
    emails = load_json(EMAILS_JSON)
    updated = False
    for e in emails:
        if e.get("id") == email_id:
            if "reply_instruction" in data:
                e["reply_instruction"] = data["reply_instruction"]
            if "draft_status" in data:
                e["draft_status"] = data["draft_status"]
            if "workflow_status" in data:
                e["workflow_status"] = data["workflow_status"]
            updated = True
            break
    if updated:
        save_json(EMAILS_JSON, emails)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Email not found"}), 404

@app.route("/drafts/status", methods=["POST"])
@login_required
def update_draft_status():
    data = request.json
    email_id = data["email_id"]
    status = data["status"]
    drafts = load_json(DRAFTS_JSON)
    for d in drafts:
        if d["email_id"] == email_id:
            d["status"] = status
    save_json(DRAFTS_JSON, drafts)
    return jsonify({"success": True})

@app.route("/action_items/mark_done", methods=["POST"])
@login_required
def mark_action_item_done():
    data = request.json
    paragraph_index = data.get("paragraph_index")
    items = load_json(ACTION_ITEMS_JSON)
    updated = False
    for item in items:
        if str(item.get("paragraph_index")) == str(paragraph_index):
            item["status"] = "done"
            updated = True
    if updated:
        save_json(ACTION_ITEMS_JSON, items)
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Action item not found"}), 404

# --- Agendas (note-taking) ---
@app.route("/agendas")
@login_required
def agendas_list():
    meetings = load_meetings()
    staff_meetings = [m for m in meetings if (m.get("agenda_type") or "staff") == "staff"]
    project_meetings = [m for m in meetings if m.get("agenda_type") == "project"]
    drive_agendas = load_drive_agendas()
    return render_template(
        "agendas_list.html",
        staff_meetings=staff_meetings,
        project_meetings=project_meetings,
        drive_agendas=drive_agendas,
    )

@app.route("/agendas/<meeting_id>")
@login_required
def agenda_detail(meeting_id):
    meetings = load_meetings()
    meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
    if not meeting:
        return redirect(url_for("agendas_list"))
    return render_template("agenda_detail.html", meeting=meeting)

@app.route("/api/meetings", methods=["GET", "POST"])
@login_required
def api_meetings():
    if request.method == "GET":
        return jsonify(load_meetings())
    data = request.json or {}
    meetings = load_meetings()
    agenda_type = (data.get("agenda_type") or "staff").lower()
    if agenda_type not in ("staff", "project"):
        agenda_type = "staff"
    new_meeting = {
        "id": str(uuid.uuid4()),
        "agenda_type": agenda_type,
        "title": data.get("title", "Untitled meeting"),
        "date": data.get("date", datetime.now().strftime("%Y-%m-%d")),
        "attendees": data.get("attendees", ""),
        "notes": data.get("notes", ""),
        "action_items": data.get("action_items", []),
    }
    meetings.append(new_meeting)
    save_meetings(meetings)
    return jsonify({"success": True, "meeting": new_meeting})

@app.route("/api/meetings/<meeting_id>", methods=["GET", "PUT"])
@login_required
def api_meeting(meeting_id):
    meetings = load_meetings()
    meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
    if not meeting:
        return jsonify({"success": False, "error": "Meeting not found"}), 404
    if request.method == "GET":
        return jsonify(meeting)
    data = request.json or {}
    if "title" in data:
        meeting["title"] = data["title"]
    if "date" in data:
        meeting["date"] = data["date"]
    if "attendees" in data:
        meeting["attendees"] = data["attendees"]
    if "notes" in data:
        meeting["notes"] = data["notes"]
    if "action_items" in data:
        meeting["action_items"] = data["action_items"]
    if "agenda_type" in data and data["agenda_type"] in ("staff", "project"):
        meeting["agenda_type"] = data["agenda_type"]
    save_meetings(meetings)
    return jsonify({"success": True, "meeting": meeting})

@app.route("/api/meetings/<meeting_id>/extract_action_items", methods=["POST"])
@login_required
def api_extract_action_items(meeting_id):
    meetings = load_meetings()
    meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
    if not meeting:
        return jsonify({"success": False, "error": "Meeting not found"}), 404
    notes = meeting.get("notes", "") or ""
    try:
        items = extract_action_items_from_notes_text(notes)
        meeting["action_items"] = items
        save_meetings(meetings)
        return jsonify({"success": True, "action_items": items})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/meetings/<meeting_id>/action_items", methods=["PUT"])
@login_required
def api_meeting_action_items(meeting_id):
    meetings = load_meetings()
    meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
    if not meeting:
        return jsonify({"success": False, "error": "Meeting not found"}), 404
    data = request.json or {}
    if "action_items" in data:
        meeting["action_items"] = data["action_items"]
        save_meetings(meetings)
    return jsonify({"success": True, "meeting": meeting})


@app.route("/api/google_docs/list", methods=["GET"])
@login_required
def api_google_docs_list():
    folder_id = request.args.get("folder_id")
    if not folder_id:
        return jsonify({"success": False, "error": "missing folder_id"}), 400

    # Prefer service account helper if available
    if HAS_GOOGLE_DOCS_INTEGRATION:
        files = list_docs_in_folder(folder_id)
        return jsonify({"success": True, "files": files})

    # Fallback to per-user OAuth credentials
    if not GOOGLE_OAUTH_AVAILABLE:
        return jsonify({"success": False, "error": "Google APIs not available on server"}), 500
    creds = _load_user_google_credentials()
    if not creds:
        return jsonify({"success": False, "error": "user Google credentials not found"}), 401
    try:
        drive = g_build("drive", "v3", credentials=creds)
        q = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and trashed=false"
        results = drive.files().list(q=q, fields="files(id,name)", pageSize=1000).execute()
        files = results.get("files", [])
        return jsonify({"success": True, "files": files})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/google_docs/extract_doc", methods=["POST"])
@login_required
def api_google_docs_extract_doc():
    # If service account helpers available, use them; otherwise use user OAuth credentials
    data = request.json or {}
    doc_id = data.get("doc_id")
    meeting_id = data.get("meeting_id")
    if not doc_id:
        return jsonify({"success": False, "error": "missing doc_id"}), 400
    items = []
    if HAS_GOOGLE_DOCS_INTEGRATION:
        items = process_google_doc_to_action_items(doc_id)
    else:
        if not GOOGLE_OAUTH_AVAILABLE:
            return jsonify({"success": False, "error": "Google APIs not available on server"}), 500
        creds = _load_user_google_credentials()
        if not creds:
            return jsonify({"success": False, "error": "user Google credentials not found"}), 401
        try:
            docs = g_build("docs", "v1", credentials=creds)
            doc = docs.documents().get(documentId=doc_id).execute()
            # extract text similar to extract_and_sync.fetch_google_doc_text
            body = doc.get("body", {})
            text_chunks = []
            for content in body.get("content", []):
                para = content.get("paragraph")
                if not para:
                    continue
                for elem in para.get("elements", []):
                    txt_run = elem.get("textRun")
                    if txt_run and txt_run.get("content"):
                        text_chunks.append(txt_run.get("content"))
            text = "".join(text_chunks).strip()
            from agendas.extract_and_sync import extract_action_items_from_notes_text
            items = extract_action_items_from_notes_text(text)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    if meeting_id:
        meetings = load_meetings()
        meeting = next((m for m in meetings if str(m.get("id")) == str(meeting_id)), None)
        if not meeting:
            return jsonify({"success": False, "error": "meeting not found"}), 404
        meeting.setdefault("action_items", [])
        meeting["action_items"].extend(items)
        save_meetings(meetings)
    else:
        action_items = load_json(ACTION_ITEMS_JSON)
        action_items.extend(items)
        save_json(ACTION_ITEMS_JSON, action_items)
    return jsonify({"success": True, "count": len(items)})


@app.route("/api/google_docs/extract_folder", methods=["POST"])
@login_required
def api_google_docs_extract_folder():
    data = request.json or {}
    folder_id = data.get("folder_id")
    if not folder_id:
        return jsonify({"success": False, "error": "missing folder_id"}), 400

    items = []
    # Service account path
    if HAS_GOOGLE_DOCS_INTEGRATION:
        items = process_google_docs_in_folder(folder_id)
    else:
        if not GOOGLE_OAUTH_AVAILABLE:
            return jsonify({"success": False, "error": "Google APIs not available on server"}), 500
        creds = _load_user_google_credentials()
        if not creds:
            return jsonify({"success": False, "error": "user Google credentials not found"}), 401
        try:
            drive = g_build("drive", "v3", credentials=creds)
            q = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and trashed=false"
            results = drive.files().list(q=q, fields="files(id,name)", pageSize=1000).execute()
            files = results.get("files", [])
            from agendas.extract_and_sync import extract_action_items_from_notes_text
            docs = g_build("docs", "v1", credentials=creds)
            for f in files:
                try:
                    doc = docs.documents().get(documentId=f.get("id")).execute()
                    body = doc.get("body", {})
                    text_chunks = []
                    for content in body.get("content", []):
                        para = content.get("paragraph")
                        if not para:
                            continue
                        for elem in para.get("elements", []):
                            txt_run = elem.get("textRun")
                            if txt_run and txt_run.get("content"):
                                text_chunks.append(txt_run.get("content"))
                    text = "".join(text_chunks).strip()
                    items.extend(extract_action_items_from_notes_text(text))
                except Exception:
                    continue
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    action_items = load_json(ACTION_ITEMS_JSON)
    action_items.extend(items)
    save_json(ACTION_ITEMS_JSON, action_items)
    return jsonify({"success": True, "count": len(items)})

# --- Kanban (project tracker) ---
@app.route("/kanban")
@login_required
def kanban_board():
    boards = load_boards()
    if not boards:
        boards = [{"id": str(uuid.uuid4()), "name": "Kanban board", "columns": {"todo": [], "in_progress": [], "done": []}}]
        save_boards(boards)
    return render_template("kanban.html", boards=boards)

@app.route("/api/boards", methods=["GET", "POST"])
@login_required
def api_boards():
    if request.method == "GET":
        return jsonify(load_boards())
    data = request.json or {}
    boards = load_boards()
    new_board = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "New board"),
        "columns": data.get("columns", {"todo": [], "in_progress": [], "done": []}),
    }
    boards.append(new_board)
    save_boards(boards)
    return jsonify({"success": True, "board": new_board})

@app.route("/api/boards/<board_id>", methods=["GET", "PUT"])
@login_required
def api_board(board_id):
    boards = load_boards()
    board = next((b for b in boards if b.get("id") == board_id), None)
    if not board:
        return jsonify({"success": False, "error": "Board not found"}), 404
    if request.method == "GET":
        return jsonify(board)
    data = request.json or {}
    if "name" in data:
        board["name"] = data["name"]
    if "columns" in data:
        board["columns"] = data["columns"]
    save_boards(boards)
    return jsonify({"success": True, "board": board})

# --- Settings ---
@app.route("/settings")
@login_required
def settings_page():
    settings = load_user_settings()
    return render_template("settings.html", settings=settings)

@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    data = request.json or {}
    settings = load_user_settings()
    if "display_name" in data:
        settings["display_name"] = data["display_name"]
    if "email" in data:
        settings["email"] = data["email"]
    save_user_settings(settings)
    if data.get("display_name"):
        session["user_name"] = data["display_name"]
    return jsonify({"success": True, "settings": settings})

def run_dashboard():
    # Prevent Flask reloader from running the menu again
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not os.environ.get("FLASK_RUN_FROM_CLI"):
        print("🚀 Starting dashboard at http://127.0.0.1:5000/")
        app.run(debug=True)

if __name__ == "__main__":
    run_dashboard()

from flask import Flask, render_template, request, jsonify, redirect, url_for
import os
import json
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

# Data directory (inside src/data)
DATA_DIR = SRC_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EMAILS_JSON = DATA_DIR / "emails.json"
ACTION_ITEMS_JSON = DATA_DIR / "action_items.json"
DRAFTS_JSON = DATA_DIR / "drafts.json"

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

@app.route("/")
def dashboard():
    emails = load_json(EMAILS_JSON)
    action_items = load_json(ACTION_ITEMS_JSON)
    drafts = load_json(DRAFTS_JSON)
    return render_template("dashboard.html", emails=emails, action_items=action_items, drafts=drafts)

@app.route("/emails_full")
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
    return render_template("action_items_full.html", grouped=grouped)

@app.route("/sync_emails", methods=["POST"])
def sync_emails():
    sync_emails_to_json(EMAILS_JSON)
    return jsonify({"success": True})

@app.route("/extract_action_items", methods=["POST"])
def extract_action_items():
    extract_action_items_from_agendas(ACTION_ITEMS_JSON)
    return jsonify({"success": True})

@app.route("/emails")
def get_emails():
    return jsonify(load_json(EMAILS_JSON))

@app.route("/action_items")
def get_action_items():
    return jsonify(load_json(ACTION_ITEMS_JSON))

@app.route("/drafts")
def get_drafts():
    return jsonify(load_json(DRAFTS_JSON))

@app.route("/drafts/generate", methods=["POST"])
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

def run_dashboard():
    # Prevent Flask reloader from running the menu again
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not os.environ.get("FLASK_RUN_FROM_CLI"):
        print("🚀 Starting dashboard at http://127.0.0.1:5000/")
        app.run(debug=True)

if __name__ == "__main__":
    run_dashboard()

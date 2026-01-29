# main.py
#!/usr/bin/env python3
"""
Email Automation Pipeline - Main Orchestrator
Syncs Outlook emails to a web dashboard and processes replies
"""

import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import os
import time
import requests
from datetime import datetime, timezone
import argparse
from flask import Flask, render_template, request, redirect, url_for, jsonify

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
# Navigate up to src directory: main.py -> main/ -> outlook/ -> src/
src_root = current_file.parent.parent.parent
# Navigate up to project root: src/ -> project_root/
project_root = src_root.parent

# Add to sys.path if not already present
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# ------------------------------------------------------------------------

# Environment selection via CLI (dev uses .env, director uses .env.director)
parser = argparse.ArgumentParser(description='Email Automation Pipeline')
parser.add_argument('--env', type=str, choices=['dev', 'director'], default='dev',
                    help='Environment to use: dev (.env) or director (.env.director)')
parser.add_argument('command', nargs='?', choices=['sync', 'draft', 'revise', 'send', 'full', 'watch'],
                    help='Command to execute')
args, unknown = parser.parse_known_args()

env_map = {'dev': '.env', 'director': '.env.director'}
env_file = current_file.parent.parent / env_map[args.env]
print(f"📄 Using environment: {args.env} ({env_file.name})")

# Load selected .env file
load_dotenv(dotenv_path=env_file)

# Data storage paths (replace Notion DB with local JSON files)
EMAILS_JSON = project_root / "data" / "emails.json"
ACTION_ITEMS_JSON = project_root / "data" / "action_items.json"
DRAFTS_JSON = project_root / "data" / "drafts.json"

# Ensure data directory exists
os.makedirs(project_root / "data", exist_ok=True)

# Optional tuning via env
WATCH_INTERVAL_SEC = int(os.getenv("NOTION_WATCH_INTERVAL_SEC", "15"))
SYNC_EVERY_SEC = int(os.getenv("NOTION_SYNC_EVERY_SEC", "300"))
INSTRUCTION_DEBOUNCE_SEC = int(os.getenv("NOTION_INSTRUCTION_DEBOUNCE_SEC", "60"))

def _print_draft_generation_tip():
    print("Tip: Drafts are generated when:")
    print(" - 'Reply Instruction' is not empty")
    print(" - 'Draft Status' is set to 'Generate Draft'")
    print(f" - The page has been idle for ≥ {INSTRUCTION_DEBOUNCE_SEC}s (debounce)")

# Token cache path relative to project root
TOKEN_CACHE_PATH = project_root / "config" / "outlook_token_cache.json"

from outlook.sync.sync_outlook_notion import sync_emails
from outlook.sync.draft_replies import process_draft_generation
from outlook.sync.revise_drafts import process_revisions
from outlook.sync.send_approved_replies import send_approved_replies

def _outlook_creds_present() -> bool:
    """Check if Outlook creds exist to allow Outlook-dependent operations."""
    required = ["OUTLOOK_CLIENT_ID", "OUTLOOK_CLIENT_SECRET", "OUTLOOK_TENANT_ID", "OUTLOOK_USER"]
    return all(os.getenv(k) for k in required)

def _ensure_outlook_or_warn(action: str) -> bool:
    """Warn and skip Outlook operations when creds are missing."""
    if _outlook_creds_present():
        return True
    print(f"⛔ {action} skipped: missing Outlook credentials. Set OUTLOOK_CLIENT_ID/SECRET/TENANT_ID/OUTLOOK_USER in {env_file.name}.")
    return False

def print_header():
    print("=" * 60)
    print("📧 EMAIL AUTOMATION PIPELINE")
    print("=" * 60)
    print(f"⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()


def print_footer():
    print()
    print("=" * 60)
    print(f"✅ Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


def run_sync_only():
    print_header()
    try:
        if _ensure_outlook_or_warn("Email sync"):
            sync_emails()
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during sync: {e}")
        sys.exit(1)


def run_draft_generation():
    print_header()
    try:
        print("Generating Draft Replies")
        _print_draft_generation_tip()
        process_draft_generation()
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during draft generation: {e}")
        sys.exit(1)


def run_revision_processing():
    print_header()
    try:
        process_revisions()
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during revision processing: {e}")
        sys.exit(1)


def run_send_approved():
    print_header()
    try:
        if _ensure_outlook_or_warn("Sending approved replies"):
            send_approved_replies()
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during sending: {e}")
        sys.exit(1)


def run_full_pipeline():
    print_header()
    
    try:
        print("STEP 1: Syncing Outlook → Notion")
        print("-" * 60)
        sync_emails()
        print()
        
        print("\nSTEP 2: Generating Draft Replies")
        print("-" * 60)
        _print_draft_generation_tip()
        process_draft_generation()
        print()
        
        print("\n⏸️  Pipeline paused for draft review")
        print("=" * 60)
        print("Next steps:")
        print("1. Review drafts in Notion dashboard")
        print("2. Set 'Draft Status' to 'Approved' to send")
        print("3. Or set to 'Needs Revision' and add notes to revise")
        print("4. Run option 5 to process revisions")
        print("5. Run option 6 to send approved replies")
        print("=" * 60)
        
        print_footer()
        
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)

# --- Flask Dashboard Setup ---
from dashboard.app import app  # Import the Flask app from your dashboard module

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

@app.route("/")
def dashboard():
    emails = load_json(EMAILS_JSON)
    action_items = load_json(ACTION_ITEMS_JSON)
    drafts = load_json(DRAFTS_JSON)
    return render_template("dashboard.html", emails=emails, action_items=action_items, drafts=drafts)

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
    # Simulate draft generation
    data = request.json
    drafts = load_json(DRAFTS_JSON)
    new_draft = {
        "email_id": data["email_id"],
        "instruction": data["instruction"],
        "draft": f"Draft reply for: {data['instruction']}",
        "status": "Generated"
    }
    drafts.append(new_draft)
    save_json(DRAFTS_JSON, drafts)
    return jsonify({"success": True, "draft": new_draft})

@app.route("/drafts/revise", methods=["POST"])
def revise_draft():
    data = request.json
    drafts = load_json(DRAFTS_JSON)
    for d in drafts:
        if d["email_id"] == data["email_id"]:
            d["draft"] = data["new_draft"]
            d["status"] = "Revised"
    save_json(DRAFTS_JSON, drafts)
    return jsonify({"success": True})

@app.route("/drafts/send", methods=["POST"])
def send_draft():
    data = request.json
    drafts = load_json(DRAFTS_JSON)
    for d in drafts:
        if d["email_id"] == data["email_id"]:
            d["status"] = "Sent"
    save_json(DRAFTS_JSON, drafts)
    # Here, integrate with Outlook send logic if needed
    return jsonify({"success": True})

def sync_emails_to_json():
    # Replace sync_emails() to fetch emails and save to EMAILS_JSON
    emails = []  # Fetch from Outlook API
    # ...fetch logic...
    save_json(EMAILS_JSON, emails)
    print("Synced emails to dashboard.")

def extract_action_items_to_json():
    # Extract action items and save to ACTION_ITEMS_JSON
    action_items = []  # Extract logic...
    # ...extract logic...
    save_json(ACTION_ITEMS_JSON, action_items)
    print("Extracted action items to dashboard.")

def run_dashboard():
    print("🚀 Starting dashboard at http://127.0.0.1:5000/")
    app.run(debug=True)

def show_menu():
    print_header()
    print(f"🔧 Current environment: {args.env} ({env_map[args.env]})")
    print("Select an option:")
    print("  1. Run full pipeline (Sync + Generate Drafts)")
    print("  2. Sync emails only (Outlook → Dashboard)")
    print("  3. Generate drafts (for pending reply instructions)")
    print("  4. Process revisions (update drafts based on feedback)")
    print("  5. Send approved replies")
    print("  6. Exit")
    print("  7. Start Dashboard (web interface)")
    print()
    
    choice = input("Enter choice (1-7): ").strip()
    
    if choice == "1":
        run_full_pipeline()
    elif choice == "2":
        sync_emails_to_json()
    elif choice == "3":
        print("Use the dashboard to generate drafts.")
    elif choice == "4":
        print("Use the dashboard to revise drafts.")
    elif choice == "5":
        print("Use the dashboard to send approved replies.")
    elif choice == "6":
        print("👋 Goodbye!")
        sys.exit(0)
    elif choice == "7":
        run_dashboard()
    else:
        print("❌ Invalid choice")

if __name__ == "__main__":
    if args.command:
        command = args.command.lower()
        if command == "sync":
            sync_emails_to_json()
        elif command == "draft":
            print("Use the dashboard to generate drafts.")
        elif command == "revise":
            print("Use the dashboard to revise drafts.")
        elif command == "send":
            print("Use the dashboard to send approved replies.")
        elif command == "full":
            run_full_pipeline()
        elif command == "watch":
            run_dashboard()
    else:
        show_menu()
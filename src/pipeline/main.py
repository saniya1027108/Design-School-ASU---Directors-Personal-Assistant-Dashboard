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

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
# Navigate up to src directory: main.py -> main/ -> outlook/ -> src/
src_root = current_file.parent.parent
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

# Data storage paths
DATA_DIR = Path(__file__).parent.parent / "data"
EMAILS_JSON = DATA_DIR / "emails.json"
ACTION_ITEMS_JSON = DATA_DIR / "action_items.json"
DRAFTS_JSON = DATA_DIR / "drafts.json"

os.makedirs(DATA_DIR, exist_ok=True)

# Import your own sync logic (update these imports as needed)
from outlook.sync.sync_outlook_json import sync_emails_to_json  # <-- update this to your new sync module
from agendas.extract_and_sync import extract_action_items_from_agendas
from dashboard.app import run_dashboard

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
        sync_emails_to_json(EMAILS_JSON)
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during sync: {e}")
        sys.exit(1)


def run_action_items_extraction():
    print_header()
    try:
        extract_action_items_from_agendas(ACTION_ITEMS_JSON)
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during action item extraction: {e}")
        sys.exit(1)


def show_menu():
    print_header()
    print("Select an option:")
    print("  1. Sync emails (Outlook → Dashboard)")
    print("  2. Extract action items (Agendas → Dashboard)")
    print("  3. Start Dashboard (web interface)")
    print("  4. Exit")
    print()
    choice = input("Enter choice (1-4): ").strip()
    if choice == "1":
        run_sync_only()
    elif choice == "2":
        run_action_items_extraction()
    elif choice == "3":
        run_dashboard()
    elif choice == "4":
        print("👋 Goodbye!")
        sys.exit(0)
    else:
        print("❌ Invalid choice")

if __name__ == "__main__":
    show_menu()
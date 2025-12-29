# main.py
#!/usr/bin/env python3
"""
Email Automation Pipeline - Main Orchestrator
Syncs Outlook emails to Notion and processes replies
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

# Notion credentials for watcher
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_VERSION = "2022-06-28"

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


def _notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }

def _get_latest_notion_edit_ts():
    """Return the most recent last_edited_time across pages (ISO string) or None."""
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return None
    try:
        url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        payload = {
            "page_size": 1,
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]
        }
        resp = requests.post(url, headers=_notion_headers(), json=payload, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        return results[0].get("last_edited_time")
    except Exception as e:
        print(f"⚠️ Notion watch: failed to fetch latest edit time: {e}")
        return None

def _db_has(filter_obj) -> bool:
    """Return True if any page matches the provided filter."""
    try:
        url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        resp = requests.post(url, headers=_notion_headers(), json={"filter": filter_obj, "page_size": 1}, timeout=20)
        resp.raise_for_status()
        return len(resp.json().get("results", [])) > 0
    except Exception as e:
        print(f"⚠️ Notion check failed: {e}")
        return False

def _has_ready_to_draft_pages_debounced() -> bool:
    """
    Return True only if there are pages with:
      - Reply Instruction not empty
      - Draft Status == 'Generate Draft'
      - last_edited_time older than debounce window
    """
    try:
        url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        payload = {
            "filter": {
                "and": [
                    {"property": "Reply Instruction", "rich_text": {"is_not_empty": True}},
                    {"property": "Draft Status", "select": {"equals": "Generate Draft"}}
                ]
            },
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            "page_size": 25
        }
        resp = requests.post(url, headers=_notion_headers(), json=payload, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return False

        now = datetime.now(timezone.utc)
        for page in results:
            ts = page.get("last_edited_time")
            if not ts:
                continue
            try:
                edited = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                idle_sec = (now - edited).total_seconds()
                if idle_sec >= INSTRUCTION_DEBOUNCE_SEC:
                    return True
            except Exception:
                # if parsing fails, be conservative: do not auto-generate
                continue
        return False
    except Exception as e:
        print(f"⚠️ Notion debounce check failed: {e}")
        return False

def run_watch():
    print_header()
    print("👀 Watching Notion for changes (auto-run workflow)...")
    print(f"⏱️  Poll: {WATCH_INTERVAL_SEC}s | Re-sync: {SYNC_EVERY_SEC}s | Debounce: {INSTRUCTION_DEBOUNCE_SEC}s")
    print("Set 'Draft Status' to 'Generate Draft' when instruction is ready.\n")

    last_seen = None
    last_sync_ts = time.time() - SYNC_EVERY_SEC

    try:
        while True:
            # Periodic email sync
            now = time.time()
            if now - last_sync_ts >= SYNC_EVERY_SEC:
                try:
                    if _ensure_outlook_or_warn("Periodic sync"):
                        print("🔄 Periodic sync: Outlook → Notion")
                        sync_emails()
                    else:
                        print("⏭️  Skipping periodic sync (no Outlook credentials).")
                except Exception as e:
                    print(f"⚠️ Periodic sync failed: {e}")
                last_sync_ts = now

            latest = _get_latest_notion_edit_ts()
            if latest and latest != last_seen:
                print(f"🔔 Detected Notion changes at {latest}. Evaluating workflow steps...")

                # 1) Revisions
                try:
                    needs_revision = _db_has({
                        "and": [
                            {"property": "Draft Status", "select": {"equals": "Needs Revision"}},
                            {"property": "Revision Notes", "rich_text": {"is_not_empty": True}}
                        ]
                    })
                    if needs_revision:
                        process_revisions()
                except Exception as e:
                    print(f"⚠️ Revision processing failed: {e}")

                # 2) Generate drafts
                try:
                    if _has_ready_to_draft_pages_debounced():
                        process_draft_generation()
                    else:
                        print("⏸️  Skipping draft generation (waiting for 'Generate Draft' + debounce).")
                except Exception as e:
                    print(f"⚠️ Draft generation failed: {e}")

                # 3) Send only approved drafts (if Outlook configured)
                try:
                    has_approved = _db_has({"property": "Draft Status", "select": {"equals": "Approved"}})
                    if has_approved:
                        if _ensure_outlook_or_warn("Sending approved replies"):
                            send_approved_replies()
                        else:
                            print("⏭️  Skipping send (no Outlook credentials).")
                except Exception as e:
                    print(f"⚠️ Sending approved replies failed: {e}")

                last_seen = latest
                print("✅ Workflow check complete.\n")

            time.sleep(WATCH_INTERVAL_SEC)

    except KeyboardInterrupt:
        print("\n🛑 Stopped watching Notion.")
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error in watch mode: {e}")
        sys.exit(1)

def show_menu():
    print_header()
    print(f"🔧 Current environment: {args.env} ({env_map[args.env]})")
    print("Select an option:")
    print("  1. Run full pipeline (Sync + Generate Drafts)")
    print("  2. Sync emails only (Outlook → Notion)")
    print("  3. Generate drafts (for pending reply instructions)")
    print("  4. Process revisions (update drafts based on feedback)")
    print("  5. Send approved replies")
    print("  6. Exit")
    print("  7. Watch Notion for changes (auto-run)")
    print()
    
    choice = input("Enter choice (1-7): ").strip()
    
    if choice == "1":
        run_full_pipeline()
    elif choice == "2":
        run_sync_only()
    elif choice == "3":
        run_draft_generation()
    elif choice == "4":
        run_revision_processing()
    elif choice == "5":
        run_send_approved()
    elif choice == "6":
        print("👋 Goodbye!")
        sys.exit(0)
    elif choice == "7":
        run_watch()
    else:
        print("❌ Invalid choice")


if __name__ == "__main__":
    if args.command:
        command = args.command.lower()
        if command == "sync":
            run_sync_only()
        elif command == "draft":
            run_draft_generation()
        elif command == "revise":
            run_revision_processing()
        elif command == "send":
            run_send_approved()
        elif command == "full":
            run_full_pipeline()
        elif command == "watch":
            run_watch()
    else:
        show_menu()
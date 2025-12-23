# main.py
#!/usr/bin/env python3
"""
Email Automation Pipeline - Main Orchestrator
Syncs Outlook emails to Notion and processes replies
"""

import sys
import os
from datetime import datetime

# --- Fix sys.path so 'outlook.sync' is importable when running from src/outlook/main ---
current_file = os.path.abspath(__file__)
project_root = os.path.abspath(os.path.join(current_file, "../../../.."))
src_root = os.path.abspath(os.path.join(current_file, "../../.."))
if src_root not in sys.path:
    sys.path.insert(0, src_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ------------------------------------------------------------------------

# If you need to reference the token cache:
TOKEN_CACHE_PATH = os.path.join(project_root, "config", "outlook_token_cache.json")
# Use TOKEN_CACHE_PATH wherever you need the token cache file

from outlook.sync.sync_outlook_notion import sync_emails
from outlook.sync.reply_outlook_notion import process_pending_replies


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
        sync_emails()
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during sync: {e}")
        sys.exit(1)


def run_reply_only():
    print_header()
    try:
        process_pending_replies()
        print_footer()
    except Exception as e:
        print(f"\n❌ Fatal error during reply processing: {e}")
        sys.exit(1)


def run_full_pipeline():
    print_header()
    
    try:
        print("STEP 1: Syncing Outlook → Notion")
        print("-" * 60)
        sync_emails()
        print()
        
        print("\nSTEP 2: Processing Pending Replies")
        print("-" * 60)
        process_pending_replies()
        
        print_footer()
        
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)


def show_menu():
    print_header()
    print("Select an option:")
    print("  1. Run full pipeline (Sync + Reply)")
    print("  2. Sync emails only (Outlook → Notion)")
    print("  3. Process replies only (Notion → Outlook)")
    print("  4. Exit")
    print()
    
    choice = input("Enter choice (1-4): ").strip()
    
    if choice == "1":
        run_full_pipeline()
    elif choice == "2":
        run_sync_only()
    elif choice == "3":
        run_reply_only()
    elif choice == "4":
        print("👋 Goodbye!")
        sys.exit(0)
    else:
        print("❌ Invalid choice")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command == "sync":
            run_sync_only()
        elif command == "reply":
            run_reply_only()
        elif command == "full":
            run_full_pipeline()
        else:
            print("Usage: python main.py [sync|reply|full]")
            sys.exit(1)
    else:
        show_menu()
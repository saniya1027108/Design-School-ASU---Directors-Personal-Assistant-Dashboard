import sys
import os

# --- Fix sys.path so 'outlook_read' and 'outlook.utils' are importable ---
current_file = os.path.abspath(__file__)
project_root = os.path.abspath(os.path.join(current_file, "../../../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ------------------------------------------------------------------------

from datetime import datetime
from .outlook_read import fetch_unread_emails
from outlook.utils.utils_notion import create_or_update_email


def sync_emails():
    print("🔄 Starting email sync: Outlook → Notion")
    print(f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    emails = fetch_unread_emails()
    
    if not emails:
        print("📭 No unread emails found")
        return
    
    print(f"\n📬 Syncing {len(emails)} emails to Notion...\n")
    
    success_count = 0
    error_count = 0
    
    for e in emails:
        try:
            create_or_update_email({
                "subject": e["subject"],
                "sender": e["sender"],
                "snippet": e["snippet"],
                "full_body": e["full_body"],
                "thread_id": e["message_id"],
                "priority": e["priority"],
                "category": e["category"],
                "received_at": e["received_at"]
            })
            success_count += 1
        except Exception as error:
            error_count += 1
            print(f"⚠️ Failed to sync '{e['subject'][:50]}...' → {str(error)}")


if __name__ == "__main__":
    sync_emails()
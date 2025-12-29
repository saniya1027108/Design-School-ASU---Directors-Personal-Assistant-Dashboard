import sys
from pathlib import Path

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
# Navigate up to src directory: sync_outlook_notion.py -> sync/-> outlook/ -> src/
src_root = current_file.parent.parent.parent
# Navigate up to project root
project_root = src_root.parent

if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# ------------------------------------------------------------------------

from datetime import datetime
from .outlook_read import fetch_unread_emails
from outlook.utils.utils_notion import create_or_update_email
import re
from html import unescape
import os
import requests
from dotenv import load_dotenv

# NEW: load env for Notion API
load_dotenv()
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_VERSION = "2022-06-28"


def _notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _set_email_property_by_message_id(message_id: str, email_text: str):
    """Find page by Message ID and set the 'Email' rich_text property."""
    if not (NOTION_API_KEY and NOTION_DATABASE_ID and message_id and email_text):
        return
    try:
        # Query page by Message ID (rich_text equals)
        query_url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        payload = {
            "filter": {"property": "Message ID", "rich_text": {"equals": message_id}},
            "page_size": 1,
        }
        resp = requests.post(query_url, headers=_notion_headers(), json=payload, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return
        page_id = results[0]["id"]

        # Update 'Email' property (plain text version of the full email)
        patch_url = f"https://api.notion.com/v1/pages/{page_id}"
        display_text = (email_text[:1900] + "...") if len(email_text) > 1900 else email_text
        patch_payload = {
            "properties": {
                "Email": {"rich_text": [{"text": {"content": display_text}}]}
            }
        }
        upd = requests.patch(patch_url, headers=_notion_headers(), json=patch_payload, timeout=20)
        upd.raise_for_status()
    except Exception as e:
        print(f"⚠️ Failed to update 'Email' property for Message ID {message_id}: {e}")


def html_to_text(html: str) -> str:
    """Convert simple HTML email body to readable plain text for Notion display."""
    if not html:
        return ""
    txt = html
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?i)</p\s*>", "\n\n", txt)
    txt = re.sub(r"(?i)<p[^>]*>", "", txt)
    txt = re.sub(r"<style.*?>.*?</style>", "", txt, flags=re.S | re.I)
    txt = re.sub(r"<script.*?>.*?</script>", "", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = unescape(txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


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
            full_html = e["full_body"] or ""
            full_text = html_to_text(full_html)

            create_or_update_email({
                "subject": e["subject"],
                "sender": e["sender"],
                "snippet": e["snippet"],
                "full_body": full_html,         # keep for backward compatibility
                "full_body_html": full_html,    # map to "Full Email (HTML)" (optional)
                "full_body_text": full_text,    # map to "Full Email (Text)" (primary)
                "thread_id": e["message_id"],
                "priority": e["priority"],
                "category": e["category"],
                "received_at": e["received_at"]
            })

            # NEW: ensure Notion column 'Email' is populated (plain text full email)
            _set_email_property_by_message_id(e["message_id"], full_text)

            success_count += 1
        except Exception as error:
            error_count += 1
            print(f"⚠️ Failed to sync '{e['subject'][:50]}...' → {str(error)}")


if __name__ == "__main__":
    sync_emails()
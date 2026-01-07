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


def _set_workflow_status_by_message_id(message_id: str, status: str):
    """Update the 'Workflow Status' property for a given Message ID."""
    if not (NOTION_API_KEY and NOTION_DATABASE_ID and message_id and status):
        return
    try:
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

        patch_url = f"https://api.notion.com/v1/pages/{page_id}"
        patch_payload = {
            "properties": {
                "Workflow Status": {"select": {"name": status}}
            }
        }
        upd = requests.patch(patch_url, headers=_notion_headers(), json=patch_payload, timeout=20)
        upd.raise_for_status()
    except Exception as e:
        print(f"⚠️ Failed to update 'Workflow status' for Message ID {message_id}: {e}")


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


def get_draft_status_for_message(message_id: str):
    """
    Fetch the current Draft Status for a given message from Notion.
    Returns the draft status string, or None if not found.
    """
    if not (NOTION_API_KEY and NOTION_DATABASE_ID and message_id):
        return None
    try:
        query_url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        payload = {
            "filter": {"property": "Message ID", "rich_text": {"equals": message_id}},
            "page_size": 1,
        }
        resp = requests.post(query_url, headers=_notion_headers(), json=payload, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        props = results[0].get("properties", {})
        draft_status = props.get("Draft Status", {}).get("select", {}).get("name")
        return draft_status
    except Exception as e:
        print(f"⚠️ Failed to fetch Draft Status for Message ID {message_id}: {e}")
        return None


def update_workflow_status_from_draft_status(message_id: str):
    """
    Update workflow status based on the current draft status.
    Call this function whenever the draft status changes in your pipeline.
    """
    draft_status = get_draft_status_for_message(message_id)
    status_map = {
        "Generate Draft": "Generating Draft",
        "Needs Revision": "Revising",
        "Approved": "Sending",
        "Sent": "Complete",
        "Pending Review": "Idle",
        "New": "Idle",
    }
    workflow_status = status_map.get(draft_status, "Idle")
    _set_workflow_status_by_message_id(message_id, workflow_status)


def is_email_already_synced(message_id: str):
    """Check if an email with this Message ID already exists in Notion."""
    if not (NOTION_API_KEY and NOTION_DATABASE_ID and message_id):
        return False
    try:
        query_url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
        payload = {
            "filter": {"property": "Message ID", "rich_text": {"equals": message_id}},
            "page_size": 1,
        }
        resp = requests.post(query_url, headers=_notion_headers(), json=payload, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return bool(results)
    except Exception as e:
        print(f"⚠️ Failed to check if email is already synced for Message ID {message_id}: {e}")
        return False


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
            # Skip emails that are already synced
            if is_email_already_synced(e["message_id"]):
                continue

            _set_workflow_status_by_message_id(e["message_id"], "Syncing")

            full_html = e["full_body"] or ""
            full_text = html_to_text(full_html)

            create_or_update_email({
                "subject": e["subject"],
                "sender": e["sender"],
                "snippet": e["snippet"],
                "full_body": full_html,
                "full_body_html": full_html,
                "full_body_text": full_text,
                "thread_id": e["message_id"],
                "priority": e["priority"],
                "category": e["category"],
                "received_at": e["received_at"]
            })

            _set_email_property_by_message_id(e["message_id"], full_text)

            update_workflow_status_from_draft_status(e["message_id"])

            success_count += 1
        except Exception as error:
            error_count += 1
            print(f"⚠️ Failed to sync '{e['subject'][:50]}...' → {str(error)}")
            _set_workflow_status_by_message_id(e["message_id"], "Error")

    # Optionally, set all emails to "Idle" after processing is done
    # for e in emails:
    #     _set_workflow_status_by_message_id(e["message_id"], "Idle")


if __name__ == "__main__":
    sync_emails()
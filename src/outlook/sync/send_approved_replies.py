"""
Send Approved Replies Module
Sends only approved draft replies from Notion dashboard
"""

import sys
from pathlib import Path

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
src_root = current_file.parent.parent.parent
project_root = src_root.parent

if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# ------------------------------------------------------------------------

import requests
from outlook.utils.outlook_auth import get_token
from outlook.utils.utils_notion import get_approved_drafts, mark_draft_sent


def send_reply(message_id, reply_body):
    """Send reply via Outlook"""
    base_url = "https://graph.microsoft.com/v1.0/me/messages"
    access_token = get_token()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Create reply draft
    draft_url = f"{base_url}/{message_id}/createReply"
    draft_res = requests.post(draft_url, headers=headers)
    draft_res.raise_for_status()
    draft_id = draft_res.json()["id"]

    # Update with full HTML body
    update_url = f"{base_url}/{draft_id}"
    update_payload = {
        "body": {
            "contentType": "HTML",
            "content": reply_body
        }
    }
    requests.patch(update_url, headers=headers, json=update_payload).raise_for_status()

    # Send
    send_url = f"{base_url}/{draft_id}/send"
    requests.post(send_url, headers=headers).raise_for_status()

    print(f"✅ Reply sent for message {message_id}")


def send_approved_replies():
    """Send all approved draft replies"""
    print("📧 Sending approved replies...\n")
    
    approved = get_approved_drafts()
    
    if not approved:
        print("No approved drafts found.")
        return
    
    sent_count = 0
    
    for page in approved:
        props = page["properties"]
        page_id = page["id"]
        
        message_id = props["Message ID"]["rich_text"][0]["text"]["content"]
        draft_reply = props["Draft Reply"]["rich_text"][0]["text"]["content"]
        
        try:
            # Send the approved draft
            send_reply(message_id, draft_reply)
            
            # Mark as sent in Notion
            mark_draft_sent(page_id, draft_reply)
            
            subject = props["Subject"]["title"][0]["text"]["content"]
            print(f"✅ Sent reply for: {subject[:50]}...")
            sent_count += 1
            
        except Exception as e:
            print(f"❌ Error sending reply for {message_id}: {e}")
    
    print(f"\n✅ Sent {sent_count} reply(ies)")


if __name__ == "__main__":
    send_approved_replies()

import os
import json
from pathlib import Path

# Import your local fetch logic (used in sync_outlook_notion.py)
from .outlook_read import fetch_unread_emails

def sync_emails_to_json(output_path):
    """
    Fetch emails using local pipeline logic and save them to the given JSON file for the dashboard.
    """
    emails = fetch_unread_emails()  # This should return a list of email dicts

    # Convert emails to a dashboard-friendly format
    # "from" = display name (org chart or API name) for Sender column; "email" = address for replies
    dashboard_emails = []
    for e in emails:
        dashboard_emails.append({
            "id": e.get("message_id"),
            "from": e.get("sender_display") or e.get("sender"),
            "subject": e.get("subject"),
            "email": e.get("sender"),
            "summary": e.get("snippet"),
            "category": e.get("category"),
            "priority": e.get("priority"),
            "date": e.get("received_at"),
            # Defaults for dashboard UX
            "reply_instruction": e.get("reply_instruction", ""),
            "draft_status": e.get("draft_status", "new"),
            "workflow_status": e.get("workflow_status", "idle"),
        })

    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", encoding="utf-8") as f:
        json.dump(dashboard_emails, f, indent=2)
    print(f"Synced {len(dashboard_emails)} emails to {outp}")

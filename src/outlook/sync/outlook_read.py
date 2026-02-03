import sys
from pathlib import Path
import json
import os

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
# Navigate up to src directory: outlook_read.py -> sync/-> outlook/ -> src/
src_root = current_file.parent.parent.parent
# Navigate up to project root
project_root = src_root.parent

if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# -----------------------------------------------------

import requests
from dotenv import load_dotenv
from outlook.utils.outlook_auth import get_token

load_dotenv()

USER = os.getenv("OUTLOOK_USER")
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

domain = USER.split('@')[-1] if USER else ""

# Load organization chart for category lookup
def load_org_chart():
    config_path = current_file.parent.parent / "config" / "organization_chart.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            return json.load(f)
    print(f"⚠️ organization_chart.json not found at {config_path}.")
    return {}

ORG_CHART = load_org_chart()

def lookup_sender_category(sender_email):
    sender_email = (sender_email or "").lower()
    for category, people in ORG_CHART.items():
        for name, email in people.items():
            if email and sender_email == email.lower():
                return category
    return "Others"


def lookup_sender_name(sender_email):
    """
    If sender_email is in organization_chart.json, return the associated name.
    Otherwise return None (caller can use API name or email).
    """
    if not sender_email:
        return None
    sender_email = sender_email.lower().strip()
    for category, people in ORG_CHART.items():
        for name, email in people.items():
            if email and sender_email == email.lower():
                return name
    return None

def determine_category_and_priority(sender_email, subject, snippet, full_body=""):
    category = lookup_sender_category(sender_email)
    # Priority mapping as per user request
    priority_map = {
        "Assistant Director": "Vips",
        "Associate Director": "Vips",
        "VIPs": "Vips",
        "Program Head": "Critical",
        "Director of Special Program": "Critical",
        "Faculty": "Critical",
        "FA": "Critical",
        "Staff": "Internal",
        "Manager": "Internal",
        "Part Time Staff": "Internal",
        "Student Worker": "Internal",
    }
    priority = priority_map.get(category, "Others")
    return category, priority


def _parse_message(m):
    """Parse a single message dict from Graph API into our format."""
    subject = m.get("subject", "") or ""
    sender_email = m.get("from", {}).get("emailAddress", {}).get("address", "") or ""
    api_name = m.get("from", {}).get("emailAddress", {}).get("name", "") or ""
    display_name = lookup_sender_name(sender_email) or api_name or sender_email
    snippet = m.get("bodyPreview", "") or ""
    message_id = m.get("id")
    received_at = m.get("receivedDateTime")
    full_body = m.get("body", {}).get("content", "") or snippet
    category, priority = determine_category_and_priority(sender_email, subject, snippet, full_body)
    return {
        "subject": subject,
        "sender": sender_email,
        "sender_display": display_name,
        "snippet": snippet,
        "full_body": full_body,
        "message_id": message_id,
        "priority": priority,
        "category": category,
        "received_at": received_at,
    }


def fetch_unread_emails():
    """Fetch all unread emails from inbox using Graph API pagination."""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    page_size = 500
    url = (
        f"{GRAPH_BASE}/me/messages"
        f"?$filter=isRead eq false"
        f"&$top={page_size}"
        f"&$select=id,subject,from,bodyPreview,receivedDateTime,body"
        f"&$orderby=receivedDateTime desc"
    )
    parsed = []

    while url:
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        mails = data.get("value", [])
        for m in mails:
            parsed.append(_parse_message(m))
        url = data.get("@odata.nextLink")

    return parsed


def fetch_message(message_id):
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_BASE}/me/messages/{message_id}?$select=subject,from,body"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def load_keywords():
    """Load keywords.json from config directory"""
    config_path = current_file.parent.parent / "config" / "keywords.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            return json.load(f)
    print(f"⚠️ keywords.json not found at {config_path}.")
    return {}

# KEYWORDS = load_keywords()  # Uncomment and use as needed

if __name__ == "__main__":
    emails = fetch_unread_emails()
    print("Fetched:", len(emails))
    for e in emails:
        print(f"Subject: {e['subject']}")
        print(f"Sender: {e.get('sender_display', e['sender'])}")
        print(f"Category: {e['category']} | Priority: {e['priority']}\n")
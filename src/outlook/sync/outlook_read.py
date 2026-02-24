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

import time
import requests
from dotenv import load_dotenv
from outlook.utils.outlook_auth import get_token

# Load .env from src/ when running from dashboard/
_src_dir = current_file.parent.parent.parent
_env_path = _src_dir / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=True)
else:
    load_dotenv()

USER = os.getenv("OUTLOOK_USER")
# Director/Paola email: only fetch emails from Paola OR emails where Mireille (USER) is CC'd
PAOLA_EMAIL = (os.getenv("PAOLA_EMAIL") or "").strip()
OUTLOOK_FETCH_TOP = int(os.getenv("OUTLOOK_FETCH_TOP", "50"))
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
    # Use body if present (single-message fetch), else bodyPreview to avoid heavy list calls
    full_body = m.get("body", {}).get("content", "") if m.get("body") else snippet
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
    """Fetch unread emails from inbox using Graph API. Only returns: from Paola OR where Mireille (USER) is in CC."""
    # Re-load env so PAOLA_EMAIL and USER are set even if module was imported before .env existed
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=True)
    user_lower = (os.getenv("OUTLOOK_USER") or USER or "").strip().lower()
    paola_lower = (os.getenv("PAOLA_EMAIL") or PAOLA_EMAIL or "").strip().lower()

    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    # Request only metadata + bodyPreview (no full body) so the list call stays fast and avoids 504
    # Include toRecipients, ccRecipients for filtering: from Paola OR Mireille (USER) in CC
    fetch_top = int(os.getenv("OUTLOOK_FETCH_TOP", "50") or "50")
    page_size = min(fetch_top, 100)
    url = (
        f"{GRAPH_BASE}/me/messages"
        f"?$filter=isRead eq false"
        f"&$top={page_size}"
        f"&$select=id,subject,from,bodyPreview,receivedDateTime,toRecipients,ccRecipients"
        f"&$orderby=receivedDateTime desc"
    )
    parsed = []
    timeout_sec = 90
    max_retries = 3

    def _keep_message(msg):
        """Only keep: from Paola, or emails where Mireille (USER) is in CC."""
        if not paola_lower and not user_lower:
            return True
        sender = (msg.get("from") or {}).get("emailAddress") or {}
        from_addr = (sender.get("address") or "").strip().lower()
        if paola_lower and from_addr == paola_lower:
            return True
        cc_list = msg.get("ccRecipients") or []
        cc_addrs = [
            (r.get("emailAddress") or {}).get("address", "") or ""
            for r in cc_list
        ]
        if user_lower and any(addr.strip().lower() == user_lower for addr in cc_addrs if addr):
            return True
        return False

    while url:
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=timeout_sec)
                resp.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in (502, 503, 504) and attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        data = resp.json()
        mails = data.get("value", [])
        for m in mails:
            if not paola_lower and not user_lower:
                parsed.append(_parse_message(m))
            elif _keep_message(m):
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
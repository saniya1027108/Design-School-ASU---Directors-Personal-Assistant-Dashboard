import sys
from pathlib import Path
import json
import os

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
# Navigate up to src directory: outlook_read.py -> sync/ -> outlook/ -> src/
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

def determine_category_and_priority(sender, subject, snippet, full_body=""):
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    snippet_lower = snippet.lower()
    body_lower = full_body.lower() if full_body else ""

    # Priority keywords (Critical if any of these appear)
    critical_keywords = ["important", "asap", "urgent", "deadline", "time sensitive", "immediately"]
    if any(kw in subject_lower or kw in snippet_lower or kw in body_lower for kw in critical_keywords):
        priority = "Critical"
    else:
        priority = "Others"

    # Category detection – highest precedence first
    if any(kw in sender_lower or kw in body_lower for kw in ["assistant director"]):
        return "Assistant Directors", priority

    if any(kw in sender_lower or kw in body_lower for kw in ["program head", "head of program", "associate head"]):
        return "Program Heads", priority

    if any(kw in sender_lower or kw in body_lower for kw in ["special program leadership", "program leader"]):
        return "Special Program Leadership", priority

    if any(kw in sender_lower or kw in body_lower for kw in ["staff leadership", "staff lead"]):
        return "Staff Leadership", priority

    if any(kw in sender_lower or kw in body_lower for kw in ["faculty", "professor", "lecturer", "clinical"]):
        return "Faculty", priority

    if sender_lower.endswith('@' + domain):
        return "Employees", "Internal"

    # Parents / Students (External)
    external_keywords = ["parent", "mom", "dad", "daughter", "son"]
    if any(kw in snippet_lower or kw in body_lower for kw in external_keywords):
        return "Parents", "External"

    # Students / Alumni
    student_keywords = ["student", "alumnus", "graduate", "alumni"]
    if any(kw in sender_lower or kw in body_lower for kw in student_keywords):
        return "Students", "Others / Students"

    return "Others", priority


def fetch_unread_emails():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_BASE}/me/messages?$filter=isRead eq false&$top=20&$select=id,subject,from,bodyPreview,receivedDateTime,body"

    resp = requests.get(url, headers=headers)
    resp.raise_for_status()

    mails = resp.json().get("value", [])
    parsed = []

    for m in mails:
        subject = m.get("subject", "") or ""
        sender_email = m.get("from", {}).get("emailAddress", {}).get("address", "") or ""
        sender_name = m.get("from", {}).get("emailAddress", {}).get("name", "") or sender_email
        sender = f"{sender_name} <{sender_email}>"
        snippet = m.get("bodyPreview", "") or ""
        message_id = m.get("id")
        received_at = m.get("receivedDateTime")
        full_body = m.get("body", {}).get("content", "") or snippet

        category, priority = determine_category_and_priority(sender, subject, snippet, full_body)

        parsed.append({
            "subject": subject,
            "sender": sender_email,  # This is the email, used for lookup in Notion sync
            "snippet": snippet,
            "full_body": full_body,
            "message_id": message_id,
            "priority": priority,
            "category": category,
            "received_at": received_at,
        })

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
        print(f"Sender: {e['sender_full']}")
        print(f"Category: {e['category']} | Priority: {e['priority']}\n")
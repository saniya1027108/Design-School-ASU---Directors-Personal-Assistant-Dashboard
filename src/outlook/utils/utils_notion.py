# utils_notion.py

import requests
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# Load organization chart for sender name/category lookup
def load_org_chart():
    current_file = os.path.abspath(__file__)
    # Point to src/outlook/config/organization_chart.json
    config_path = os.path.abspath(os.path.join(current_file, "../../config/organization_chart.json"))
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    print(f"⚠️ organization_chart.json not found at {config_path}. Sender lookup will be limited.")
    return {}

ORG_CHART = load_org_chart()

import re

# if name and email is not present in the organization_chart - extract sender name from signature
def extract_name_from_signature(email_body):
    signature_patterns = [
        r"(?:Thanks|Thank you|Best|Regards|Sincerely|Kind regards|Warm regards|Cheers|Respectfully)[,\s\n]*([A-Z][a-z]+(?: [A-Z][a-z]+)+)",
        r"^--+\s*\n?([A-Z][a-z]+(?: [A-Z][a-z]+)+)",
    ]
    for pattern in signature_patterns:
        match = re.search(pattern, email_body, re.MULTILINE)
        if match:
            return match.group(1).strip()
    # Fallback: last non-empty line
    lines = [l.strip() for l in email_body.splitlines() if l.strip()]
    if lines:
        last_line = lines[-1]
        if 2 <= len(last_line.split()) <= 4 and all(w[0].isupper() for w in last_line.split()):
            return last_line
    return None

def lookup_sender_name_and_category(sender_email, email_body=None):
    for category, people in ORG_CHART.items():
        for name, email in people.items():
            if sender_email.lower() == email.lower():
                return name, category
    # Not found: try to extract from signature if body is provided
    if email_body:
        name = extract_name_from_signature(email_body)
        if name:
            return name, None
    return None, None

def classify_response_effort(full_body):
    length = len(full_body or "")
    if length < 500:
        return "Quick"
    elif length < 1500:
        return "Moderate"
    else:
        return "High"

def get_page_by_message_id(message_id):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Message ID",
            "rich_text": {"equals": message_id},
        }
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None

def generate_better_summary(email_body):
    """Use OpenAI (gpt-4o) to generate a high-quality summary."""
    prompt = f"""
You are an expert executive assistant for a university design school director.
Summarize the following email in 2–3 concise, professional sentences. Your summary must clearly capture:
- The sender’s intent and main request
- Any key details, questions, or action items
- The overall context, so a director can quickly understand what is needed

Be specific, objective, and do not omit important context. If the sender’s name or role is mentioned in the signature or body, include it. Limit to 120 words.

Email content:
{email_body[:6000]}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a precise and professional summarizer. Always provide a complete, context-rich summary."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠️ OpenAI summarization failed: {e}")
        return email_body.strip()[:500] + ("..." if len(email_body) > 500 else "")


def create_email(data):
    url = "https://api.notion.com/v1/pages"

    received_date = None
    if data.get("received_at"):
        try:
            received_at = data["received_at"].replace("Z", "+00:00")
            received_date = datetime.fromisoformat(received_at).isoformat()
        except Exception:
            pass

    # Use full email body for summarization
    full_body = data.get("full_body", data["snippet"])
    summary = generate_better_summary(full_body)

    # Lookup sender name and category, fallback to signature extraction
    sender_email = data.get("sender", "")
    sender_name, org_category = lookup_sender_name_and_category(sender_email, full_body)
    sender_display = sender_name if sender_name else sender_email
    notion_category = org_category if org_category else data.get("category", "Others")

    # Classify response effort
    response_effort = classify_response_effort(full_body)

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Subject": {"title": [{"text": {"content": data["subject"][:1900]}}]},
            "Sender": {"rich_text": [{"text": {"content": sender_display[:1900]}}]},
            "Summary": {"rich_text": [{"text": {"content": summary}}]},
            "Priority": {"select": {"name": data["priority"]}},
            "Category": {"select": {"name": notion_category}} ,
            "Message ID": {"rich_text": [{"text": {"content": data["thread_id"]}}]},
            "Status": {"select": {"name": "New"}},
            "Response Effort": {"select": {"name": response_effort}},
        }
    }

    if received_date:
        payload["properties"]["Date Received"] = {"date": {"start": received_date}}

    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()

def create_or_update_email(data):
    page = get_page_by_message_id(data["thread_id"])
    if page:
        pass  # Skip updates to avoid duplicates
    else:
        create_email(data)

def get_pending_replies():
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Status",
            "select": {"equals": "Pending Send"},
        },
        "sorts": [{"property": "Date Received", "direction": "descending"}]
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()["results"]

def update_notion_sent(page_id, sent_body):
    now = datetime.utcnow().isoformat()
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            "Status": {"select": {"name": "Sent"}},
            "Sent Reply": {"rich_text": [{"text": {"content": sent_body}}]},
            "Reply Sent On": {"date": {"start": now}}
        }
    }
    resp = requests.patch(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
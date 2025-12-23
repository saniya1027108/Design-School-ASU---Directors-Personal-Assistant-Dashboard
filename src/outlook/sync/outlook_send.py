# outlook_send.py
import sys
import os

# --- Fix sys.path so 'outlook.utils' is importable ---
current_file = os.path.abspath(__file__)
project_root = os.path.abspath(os.path.join(current_file, "../../../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# -----------------------------------------------------

import requests
from dotenv import load_dotenv
from outlook.utils.outlook_auth import get_token

load_dotenv()

USER = os.getenv("OUTLOOK_USER")
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def send_email(to, subject, body):
    token = get_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    email_payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True
    }

    url = f"{GRAPH_BASE}/users/{USER}/sendMail"

    resp = requests.post(url, headers=headers, json=email_payload)
    resp.raise_for_status()

    print("Email sent ✔")
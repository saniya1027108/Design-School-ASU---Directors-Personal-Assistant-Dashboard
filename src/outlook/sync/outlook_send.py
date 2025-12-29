# outlook_send.py
import sys
from pathlib import Path
import os

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
# Navigate up to src directory: outlook_send.py -> sync/ -> outlook/ -> src/
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
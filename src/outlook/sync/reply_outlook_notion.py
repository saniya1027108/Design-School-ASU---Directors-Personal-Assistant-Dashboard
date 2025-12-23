# reply_outlook_notion.py  (updated to fix truncation and improve reliability)

import sys
import os

# --- Fix sys.path so 'outlook.utils' and 'outlook_read' are importable ---
current_file = os.path.abspath(__file__)
project_root = os.path.abspath(os.path.join(current_file, "../../../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ------------------------------------------------------------------------

from dotenv import load_dotenv
from openai import OpenAI  # Using OpenAI for replies (consistent with your summarization choice)
import requests
import textwrap

from outlook.utils.outlook_auth import get_token
from .outlook_read import fetch_message
from outlook.utils.utils_notion import get_pending_replies, update_notion_sent
import json


load_dotenv()

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

def lookup_sender_category(sender_email):
    for category, people in ORG_CHART.items():
        for name, email in people.items():
            if sender_email.lower() == email.lower():
                return category
    return None

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # Use same key as summarization


def generate_reply(instruction, original_body, sender_name, sender_category=None):
    # Custom system prompt based on sender category
    if sender_category == "Assistant Director":
        system_prompt = "You are a precise executive assistant. Use a collegial, collaborative, and respectful tone. Address the Assistant Director as a peer, but maintain professionalism."
    elif sender_category == "Faculty":
        system_prompt = "You are a precise executive assistant. Use a respectful, collegial, and supportive tone for faculty."
    elif sender_category == "Staff":
        system_prompt = "You are a precise executive assistant. Use a friendly, clear, and supportive tone for staff."
    elif sender_category == "Student Worker" or sender_category == "Part Time Staff":
        system_prompt = "You are a precise executive assistant. Use a warm, encouraging, and clear tone for students and part-time staff."
    else:
        system_prompt = "You are a precise executive assistant. Always complete your response fully."

    prompt = f"""
You are Paula Sanguinetti, Director of The Design School at Arizona State University.
Write a complete, warm, professional email reply in clean HTML format.

Requirements:
- Start with a personalized greeting: "Dear {sender_name}," or "Hi {sender_name.split()[0]}," if appropriate.
- Directly and clearly address the director's instruction.
- Keep tone polite, positive, and concise.
- End with a professional closing ("Best regards," or "Thanks,") followed by full signature:
  Best regards,<br>
  <strong>Paula Sanguinetti</strong><br>
  Director, The Design School<br>
  Arizona State University

- Use <p> for paragraphs and <br> for line breaks.
- Write complete sentences — never truncate or cut off mid-sentence.
- Do NOT include subject, date, or any placeholders.

Director's instruction:
{instruction}

Original email (for context only):
{original_body[:6000]}

Sender's name (use in greeting):
{sender_name}

Now write ONLY the full HTML email body:
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=800
        )
        reply_html = response.choices[0].message.content.strip()
        # Safety check: ensure signature is present and response is not truncated
        if "Paula Sanguinetti" not in reply_html:
            reply_html += textwrap.dedent("""
                <br><br>
                Best regards,<br>
                <strong>Paula Sanguinetti</strong><br>
                Director, The Design School<br>
                Arizona State University
            """).strip()
        if not reply_html.strip().endswith((".", "!", "?", "</strong>", "University")):
            reply_html += "<br><br>Best regards,<br><strong>Paula Sanguinetti</strong><br>Director, The Design School<br>Arizona State University"
        return reply_html
    except Exception as e:
        print(f"⚠️ OpenAI reply generation failed: {e}")
        return f"""
        <p>Dear {sender_name.split()[0] if sender_name else "Colleague"},</p>
        <p>Thank you for your email. I will follow up on your request shortly.</p>
        <p>Best regards,<br>
        <strong>Paula Sanguinetti</strong><br>
        Director, The Design School<br>
        Arizona State University</p>
        """


def send_reply(message_id, reply_body):
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

    print(f"Reply sent for message {message_id}")



def process_pending_replies():
    pendings = get_pending_replies()

    if not pendings:
        print("No pending replies found.")
        return

    for page in pendings:
        props = page["properties"]

        message_id = props["Message ID"]["rich_text"][0]["text"]["content"]
        instruction = props["Reply Instruction"]["rich_text"][0]["text"]["content"]

        original = fetch_message(message_id)
        original_body = original["body"]["content"]

        sender_email = original.get("from", {}).get("emailAddress", {}).get("address", "")
        sender_name = original.get("from", {}).get("emailAddress", {}).get("name", "") or sender_email.split('@')[0].replace('.', ' ').title()
        sender_category = lookup_sender_category(sender_email)

        try:
            reply_html = generate_reply(instruction, original_body, sender_name, sender_category)
            send_reply(message_id, reply_html)
            update_notion_sent(page["id"], reply_html)

            subject = props["Subject"]["title"][0]["text"]["content"]
            print(f"Replied to: {subject}")
        except Exception as e:
            print(f"Error processing {message_id}: {e}")

    print("Done processing replies.")


if __name__ == "__main__":
    process_pending_replies()
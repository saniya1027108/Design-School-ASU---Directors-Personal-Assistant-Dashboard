# reply_outlook_notion.py  (updated to fix truncation and improve reliability)

import sys
from pathlib import Path
import os

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
# Navigate up to src directory: reply_outlook_notion.py -> sync/-> outlook/ -> src/
src_root = current_file.parent.parent.parent
# Navigate up to project root
project_root = src_root.parent

if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# ------------------------------------------------------------------------

from dotenv import load_dotenv
from openai import OpenAI
import requests
import textwrap
import re
from html import unescape

from outlook.utils.outlook_auth import get_token
from .outlook_read import fetch_message
from outlook.utils.utils_notion import get_pending_replies, update_notion_sent
import json


load_dotenv()

# Load organization chart for sender name/category lookup
def load_org_chart():
    """Load organization_chart.json from config directory"""
    config_path = current_file.parent.parent / "config" / "organization_chart.json"
    if config_path.exists():
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


def sanitize_html_model_output(text: str) -> str:
    """
    Remove markdown/code fences and stray language tags like ```html or '''html.
    Keep only raw HTML content.
    """
    if not text:
        return ""

    s = text.strip()

    # If wrapped in triple backticks with optional language (```html ... ```
    if s.lower().startswith("```"):
        # remove first fence line
        parts = s.split("\n", 1)
        s = parts[1] if len(parts) > 1 else ""
        # remove closing fence if present
        if s.strip().endswith("```"):
            s = s[: s.rfind("```")].strip()

    # If wrapped in triple single quotes ('''html ... ''')
    elif s.lower().startswith("'''"):
        parts = s.split("\n", 1)
        s = parts[1] if len(parts) > 1 else ""
        if s.strip().endswith("'''"):
            s = s[: s.rfind("'''")].strip()

    # Remove any leading 'html' language token
    if s.lower().startswith("html"):
        s = s[4:].lstrip()

    # Remove any remaining lone fences inside
    s = re.sub(r"```+|'''+", "", s)

    return s.strip()


def html_to_text(html: str) -> str:
    """
    Convert simple HTML emails to readable plain text for Notion display.
    """
    if not html:
        return ""
    txt = html
    # normalize breaks and paragraphs
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?i)</p\s*>", "\n\n", txt)
    txt = re.sub(r"(?i)<p[^>]*>", "", txt)
    # strip remaining tags
    txt = re.sub(r"<[^>]+>", "", txt)
    # unescape entities and collapse excessive newlines
    txt = unescape(txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


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
- Output must be RAW HTML only. Do not wrap in Markdown code fences. Do not prefix with language names like 'html'.
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

        # NEW: sanitize out any code fences or language tokens
        reply_html = sanitize_html_model_output(reply_html)

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
            # send sanitized HTML
            send_reply(message_id, reply_html)

            # NEW: save plain text to Notion to avoid showing HTML tags
            reply_text_for_notion = html_to_text(reply_html)
            update_notion_sent(page["id"], reply_text_for_notion)

            subject = props["Subject"]["title"][0]["text"]["content"]
            print(f"Replied to: {subject}")
        except Exception as e:
            print(f"Error processing {message_id}: {e}")

    print("Done processing replies.")


if __name__ == "__main__":
    process_pending_replies()
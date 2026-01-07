"""
Draft Reply Generation Module
Generates draft replies and saves them to Notion for review
"""

import sys
from pathlib import Path
import os

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
src_root = current_file.parent.parent.parent
project_root = src_root.parent

if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# ------------------------------------------------------------------------

from dotenv import load_dotenv
from openai import OpenAI
import json
from .outlook_read import fetch_message
from outlook.utils.utils_notion import get_pending_replies, save_draft_reply
from outlook.sync.sync_outlook_notion import _set_workflow_status_by_message_id, update_workflow_status_from_draft_status

load_dotenv()

# Load organization chart
def load_org_chart():
    config_path = current_file.parent.parent / "config" / "organization_chart.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            return json.load(f)
    print(f"⚠️ organization_chart.json not found at {config_path}.")
    return {}

ORG_CHART = load_org_chart()

def lookup_sender_category(sender_email):
    for category, people in ORG_CHART.items():
        for name, email in people.items():
            if sender_email.lower() == email.lower():
                return category
    return None

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generate_draft_reply(instruction, original_body, sender_name, sender_category=None, revision_notes=None):
    """Generate a draft reply based on instruction and optional revision notes"""
    
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

    revision_section = ""
    if revision_notes:
        revision_section = f"""

IMPORTANT - REVISION REQUESTED:
The director has reviewed a previous draft and requested the following changes:
{revision_notes}

Please incorporate these changes into your response.
"""

    prompt = f"""
You are Paola Sanguinetti, Director of The Design School at Arizona State University.
Write a complete, warm, professional email reply in clean HTML format.

Requirements:
- Start with a personalized greeting: "Dear {sender_name}," or "Hi {sender_name.split()[0]}," if appropriate.
- Directly and clearly address the director's instruction.
- Keep tone polite, positive, and concise.
- End with a professional closing ("Best regards," or "Thanks,") followed by full signature:
  Best regards,<br>
  <strong>Paola Sanguinetti</strong><br>
  Director, The Design School<br>
  Arizona State University

- Use <p> for paragraphs and <br> for line breaks.
- Write complete sentences — never truncate or cut off mid-sentence.
- Do NOT include subject, date, or any placeholders.

Director's instruction:
{instruction}
{revision_section}

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
        
        # Safety check: ensure signature is present
        if "Paola Sanguinetti" not in reply_html:
            reply_html += "\n<br><br>Best regards,<br><strong>Paola Sanguinetti</strong><br>Director, The Design School<br>Arizona State University"
        
        return reply_html
    except Exception as e:
        print(f"⚠️ OpenAI draft generation failed: {e}")
        return f"""
        <p>Dear {sender_name.split()[0] if sender_name else "Colleague"},</p>
        <p>Thank you for your email. I will follow up on your request shortly.</p>
        <p>Best regards,<br>
        <strong>Paola Sanguinetti</strong><br>
        Director, The Design School<br>
        Arizona State University</p>
        """


def process_draft_generation():
    """Generate drafts for all emails with reply instructions but no draft yet"""
    print("📝 Generating draft replies...\n")
    
    pendings = get_pending_replies()
    
    if not pendings:
        print("No pending reply instructions found.")
        return
    
    draft_count = 0
    
    for page in pendings:
        props = page["properties"]
        page_id = page["id"]
        
        # Skip if draft already exists and is pending review
        draft_status = props.get("Draft Status", {}).get("select", {})
        if draft_status and draft_status.get("name") in ["Pending Review", "Approved"]:
            continue
        
        message_id = props["Message ID"]["rich_text"][0]["text"]["content"]
        instruction = props["Reply Instruction"]["rich_text"][0]["text"]["content"]

        try:
            # Set workflow status to "Generating Draft"
            _set_workflow_status_by_message_id(message_id, "Generating Draft")

            # Fetch original message
            original = fetch_message(message_id)
            original_body = original["body"]["content"]
            sender_email = original.get("from", {}).get("emailAddress", {}).get("address", "")
            sender_name = original.get("from", {}).get("emailAddress", {}).get("name", "") or sender_email.split('@')[0].replace('.', ' ').title()
            sender_category = lookup_sender_category(sender_email)
            
            # Generate draft
            draft_html = generate_draft_reply(instruction, original_body, sender_name, sender_category)
            
            # Save to Notion for review
            save_draft_reply(page_id, draft_html)

            # After saving, update workflow status based on current draft status
            update_workflow_status_from_draft_status(message_id)

            subject = props["Subject"]["title"][0]["text"]["content"]
            print(f"✅ Draft created for: {subject[:50]}...")
            draft_count += 1
            
        except Exception as e:
            _set_workflow_status_by_message_id(message_id, "Error")
            print(f"❌ Error processing {message_id}: {e}")
    
    print(f"\n✅ Generated {draft_count} draft(s)")


if __name__ == "__main__":
    process_draft_generation()

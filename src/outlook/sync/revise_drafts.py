"""
Draft Revision Module
Handles revision requests from Notion dashboard
"""

import sys
from pathlib import Path

# --- Fix sys.path using pathlib for cross-platform compatibility ---
current_file = Path(__file__).resolve()
src_root = current_file.parent.parent.parent
project_root = src_root.parent

if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# ------------------------------------------------------------------------

from .outlook_read import fetch_message
from outlook.utils.utils_notion import get_revision_requests, save_draft_reply
from .draft_replies import generate_draft_reply, lookup_sender_category
from outlook.sync.sync_outlook_notion import _set_workflow_status_by_message_id, update_workflow_status_from_draft_status


def process_revisions():
    """Process all revision requests and generate updated drafts"""
    print("🔄 Processing draft revisions...\n")
    
    revisions = get_revision_requests()
    
    if not revisions:
        print("No revision requests found.")
        return
    
    revision_count = 0
    
    for page in revisions:
        props = page["properties"]
        page_id = page["id"]
        
        message_id = props["Message ID"]["rich_text"][0]["text"]["content"]
        instruction = props["Reply Instruction"]["rich_text"][0]["text"]["content"]
        revision_notes = props["Revision Notes"]["rich_text"][0]["text"]["content"]
        
        try:
            # Set workflow status to "Revising"
            _set_workflow_status_by_message_id(message_id, "Revising")

            # Fetch original message
            original = fetch_message(message_id)
            original_body = original["body"]["content"]
            sender_email = original.get("from", {}).get("emailAddress", {}).get("address", "")
            sender_name = original.get("from", {}).get("emailAddress", {}).get("name", "") or sender_email.split('@')[0].replace('.', ' ').title()
            sender_category = lookup_sender_category(sender_email)
            
            # Generate revised draft
            revised_draft = generate_draft_reply(
                instruction, 
                original_body, 
                sender_name, 
                sender_category,
                revision_notes=revision_notes
            )
            
            # Save revised draft to Notion
            save_draft_reply(page_id, revised_draft)

            # After saving, update workflow status based on current draft status
            update_workflow_status_from_draft_status(message_id)

            subject = props["Subject"]["title"][0]["text"]["content"]
            print(f"✅ Revised draft for: {subject[:50]}...")
            revision_count += 1
            
        except Exception as e:
            _set_workflow_status_by_message_id(message_id, "Error")
            print(f"❌ Error processing revision for {message_id}: {e}")
    
    print(f"\n✅ Processed {revision_count} revision(s)")


if __name__ == "__main__":
    process_revisions()

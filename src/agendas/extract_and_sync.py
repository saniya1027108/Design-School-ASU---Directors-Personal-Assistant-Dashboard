"""
extract_and_sync.py

- Parse .docx agendas from AGENDA_FOLDER
- Extract action items via an OpenAI-compatible LLM
- Save action_items.json to RESULTS_FOLDER
- Upsert into a Notion database (dedupe using External ID)

Run:
    python extract_and_sync.py
"""

import os
import json
import hashlib
import requests
from glob import glob
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path

from docx import Document
from dotenv import load_dotenv
import os
import logging
# Optional Google APIs (service account or installed)
try:
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    GOOGLE_LIBS_AVAILABLE = True
except Exception:
    GOOGLE_LIBS_AVAILABLE = False
import dateparser


# =========================
# Config / Environment
# =========================

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

NOTION_API_KEY = os.getenv("NOTION_API_KEY_ACTION_ITEMS")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID_ACTION_ITEMS")
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

# Optional mapping NAME:NOTION_USER_ID, e.g. "Paola:uuid1,Luciana:uuid2"
PERSON_TO_NOTION_ID_ENV = os.getenv("PERSON_TO_NOTION_ID", "")
PERSON_TO_NOTION_ID: Dict[str, str] = {}
if PERSON_TO_NOTION_ID_ENV:
    for pair in PERSON_TO_NOTION_ID_ENV.split(","):
        if ":" in pair:
            name, nid = pair.split(":", 1)
            PERSON_TO_NOTION_ID[name.strip().lower()] = nid.strip()

# Hardcoded paths (keep yours)
AGENDA_FOLDER = r"C:\Users\smulla1\Desktop\Personal Assistant\Email_Notion_Sync\Design-School-ASU---Directors-Personal-Assistant-Dashboard\src\agendas\agenda_documents"
RESULTS_FOLDER = r"C:\Users\smulla1\Desktop\Personal Assistant\Email_Notion_Sync\Design-School-ASU---Directors-Personal-Assistant-Dashboard\src\agendas\results"

# Notion property names (change if your database differs)
PROP_NAME = "Name"
PROP_STATUS = "Status"
PROP_DUE = "Due"
PROP_ASSIGNEE = "Assignee"
PROP_CONTEXT = "Context"
PROP_SOURCE_DOC = "Source Document"
PROP_PARAGRAPH_INDEX = "Paragraph Index"
PROP_EXTERNAL_ID = "External ID"

DEFAULT_STATUS = "To do"


# =========================
# Utilities (docx)
# =========================

# DONE/WORKING section detection
DONE_SECTION_PREFIXES = ("DONE -", "DONE:", "COMPLETED -", "COMPLETED:")
WORKING_SECTION_PREFIXES = ("[working on]", "WORKING ON", "IN PROGRESS", "[in progress]")

def parse_docx(path: str) -> List[Dict]:
    """
    Return list of paragraphs with index, text, style, plus formatting signals.
    Computes strikethrough from runs and stores status_hint.
    """
    doc = Document(path)
    paragraphs = []

    current_section_status = None  # "done" / "todo" / None

    for idx, p in enumerate(doc.paragraphs):
        text = (p.text or "").strip()
        if not text:
            continue

        # detect section headers like "DONE - Fri 1/9/2026 ..."
        upper = text.strip().upper()
        if any(upper.startswith(prefix) for prefix in DONE_SECTION_PREFIXES):
            current_section_status = "done"
        elif any(upper.startswith(prefix.upper()) for prefix in WORKING_SECTION_PREFIXES):
            current_section_status = "todo"

        # compute strikethrough ratio from runs
        total_chars = 0
        strike_chars = 0
        has_strike = False
        for run in p.runs:
            run_text = run.text or ""
            if not run_text:
                continue
            run_len = len(run_text)
            total_chars += run_len

            is_struck = bool(run.font and run.font.strike)
            if is_struck:
                has_strike = True
                strike_chars += run_len

        strike_ratio = (strike_chars / total_chars) if total_chars else 0.0

        # choose status hint:
        # - if in DONE section => done
        # - else if mostly struck => done
        # - else todo
        if current_section_status == "done":
            status_hint = "done"
        elif has_strike and strike_ratio >= 0.60:
            status_hint = "done"
        else:
            status_hint = "todo"

        style = getattr(p, "style", None)
        style_name = style.name if style else ""

        paragraphs.append({
            "index": idx,
            "text": text,
            "style": style_name,
            "has_strike": has_strike,
            "strike_ratio": round(strike_ratio, 3),
            "section_status_hint": current_section_status,
            "status_hint": status_hint,
        })

    return paragraphs

def join_paragraphs(pars: List[Dict]) -> str:
    """
    Join with status hints so LLM can extract completed/crossed-out items too.
    Format:
      0007 [DONE] (strike=0.85, section=done) Add to Nick agenda...
    """
    lines = []
    for p in pars:
        tag = "[DONE]" if p.get("status_hint") == "done" else "[TODO]"
        meta = f"(strike={p.get('strike_ratio', 0.0)}, section={p.get('section_status_hint')})"
        lines.append(f"{p['index']:04d} {tag} {meta} {p['text']}")
    return "\n".join(lines)


# =========================
# LLM extraction
# =========================

# IMPORTANT: Escape braces {{ }} because we use .format(meeting_text=...)
EXTRACTION_PROMPT_TEMPLATE = """
You are a JSON-only extractor. Extract action items from the meeting notes below.

Each line begins with a paragraph index and a status hint tag:
- If tagged [DONE], the extracted action item MUST have "status": "done"
- If tagged [TODO], the extracted action item MUST have "status": "todo" unless the text clearly indicates it is already completed.

Return a JSON array of objects exactly like:
[
  {{
    "text": "short description (required)",
    "owner": "Full Name or null",
    "owner_email": "email or null",
    "due_date": "YYYY-MM-DD or null",
    "priority": "low|medium|high",
    "status": "todo|done",
    "context": "one sentence summary of where it came from",
    "paragraph_index": 12
  }},
  ...
]

Meeting notes:
{meeting_text}

Important: Return only valid JSON array. If there are no action items, return [].
"""

# For raw meeting notes (no paragraph index / status tags) - e.g. from web note-taking tool
EXTRACTION_PROMPT_RAW_NOTES = """
You are a JSON-only extractor. Extract action items and to-dos from the meeting notes below.
The notes are free-form (agendas, discussion points, decisions). Identify any actionable items, tasks, or to-dos.

Return a JSON array of objects exactly like:
[
  {{
    "text": "short description (required)",
    "owner": "Full Name or null",
    "owner_email": "email or null",
    "due_date": "YYYY-MM-DD or null",
    "priority": "low|medium|high",
    "status": "todo",
    "context": "one sentence summary of where it came from",
    "paragraph_index": 0
  }},
  ...
]

Meeting notes:
{meeting_text}

Important: Return only valid JSON array. If there are no action items, return [].
"""

def call_openai_chat_completion(prompt: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set.")

    url = f"{OPENAI_API_BASE.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 1500,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    j = r.json()
    return j["choices"][0]["message"]["content"]

def extract_action_items_with_llm(meeting_text: str) -> List[Dict]:
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(meeting_text=meeting_text)
    raw = call_openai_chat_completion(prompt)

    # Parse JSON robustly
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(raw[start:end + 1])
        else:
            raise ValueError("Could not parse JSON array from LLM output.")

    # Normalize keys
    normalized = []
    for item in parsed:
        status = (item.get("status") or "todo").strip().lower()
        if status not in ("todo", "done"):
            status = "todo"

        normalized.append({
            "text": item.get("text") or "",
            "owner": item.get("owner", None),
            "owner_email": item.get("owner_email", None),
            "due_date": item.get("due_date", None),
            "priority": item.get("priority", "medium"),
            "status": status,
            "context": item.get("context", None),
            "paragraph_index": item.get("paragraph_index", None),
        })
    return normalized


def extract_action_items_from_notes_text(meeting_notes: str) -> List[Dict]:
    """
    Extract action items from raw meeting notes (e.g. pasted in web note-taking tool).
    Uses a simpler prompt than docx-based extraction. Returns list of normalized action items.
    """
    if not (meeting_notes or meeting_notes.strip()):
        return []
    prompt = EXTRACTION_PROMPT_RAW_NOTES.format(meeting_text=meeting_notes.strip())
    raw = call_openai_chat_completion(prompt)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(raw[start : end + 1])
        else:
            return []
    normalized = []
    for i, item in enumerate(parsed):
        status = (item.get("status") or "todo").strip().lower()
        if status not in ("todo", "done"):
            status = "todo"
        normalized.append({
            "text": item.get("text") or "",
            "owner": item.get("owner", None),
            "owner_email": item.get("owner_email", None),
            "due_date": item.get("due_date", None),
            "priority": item.get("priority", "medium"),
            "status": status,
            "context": item.get("context", None),
            "paragraph_index": item.get("paragraph_index", i),
        })
    return normalized


# -------------------------
# Google Docs helpers
# -------------------------
def _get_google_credentials():
    """Try to build credentials from a service account JSON pointed to by
    the env var `GOOGLE_SERVICE_ACCOUNT_FILE`. Returns None if not available.
    """
    sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not sa_path:
        return None
    if not GOOGLE_LIBS_AVAILABLE:
        return None
    scopes = [
        "https://www.googleapis.com/auth/documents.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    try:
        creds = service_account.Credentials.from_service_account_file(sa_path, scopes=scopes)
        return creds
    except Exception as e:
        logging.warning("Failed to load service account credentials: %s", e)
        return None


def fetch_google_doc_text(doc_id: str) -> Optional[str]:
    """Fetch plain text from a Google Doc using the Docs API.
    Requires `GOOGLE_SERVICE_ACCOUNT_FILE` or the google libs present.
    Returns the concatenated text or None on error.
    """
    creds = _get_google_credentials()
    if not creds:
        return None
    try:
        service = build("docs", "v1", credentials=creds)
        doc = service.documents().get(documentId=doc_id).execute()
        body = doc.get("body", {})
        text_chunks = []
        for content in body.get("content", []):
            para = content.get("paragraph")
            if not para:
                continue
            for elem in para.get("elements", []):
                txt_run = elem.get("textRun")
                if txt_run and txt_run.get("content"):
                    text_chunks.append(txt_run.get("content"))
        return "".join(text_chunks).strip()
    except Exception as e:
        logging.exception("Error fetching Google Doc %s: %s", doc_id, e)
        return None


def list_docs_in_folder(folder_id: str) -> List[Dict[str, str]]:
    """List Google Docs (mime type application/vnd.google-apps.document) in a Drive folder.
    Returns list of {id, name}. Requires service account or Drive API access.
    """
    creds = _get_google_credentials()
    if not creds:
        return []
    try:
        drive = build("drive", "v3", credentials=creds)
        q = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and trashed=false"
        results = drive.files().list(q=q, fields="files(id,name)", pageSize=1000).execute()
        files = results.get("files", [])
        return files
    except Exception as e:
        logging.exception("Error listing files in folder %s: %s", folder_id, e)
        return []


def process_google_doc_to_action_items(doc_id: str, source_label: str = None) -> List[Dict]:
    """Fetch a Google Doc, extract action items using the LLM extractor, and return list of items.
    This function does not persist to Notion automatically; callers may save or upsert as needed.
    """
    text = fetch_google_doc_text(doc_id)
    if not text:
        return []
    # Use raw notes extractor so we don't rely on paragraph indices
    items = extract_action_items_from_notes_text(text)
    # annotate source
    for it in items:
        it.setdefault("source", source_label or f"gdoc:{doc_id}")
    return items


def process_google_docs_in_folder(folder_id: str, source_label: str = None) -> List[Dict]:
    """List docs in a folder and extract action items from each; returns flattened list."""
    files = list_docs_in_folder(folder_id)
    all_items: List[Dict] = []
    for f in files:
        doc_id = f.get("id")
        label = source_label or f.get("name") or f"gdoc:{doc_id}"
        try:
            items = process_google_doc_to_action_items(doc_id, source_label=label)
            all_items.extend(items)
        except Exception:
            logging.exception("Failed processing doc %s", doc_id)
    return all_items


# =========================
# Notion helpers (upsert)
# =========================

def notion_headers() -> Dict[str, str]:
    if not NOTION_API_KEY:
        raise RuntimeError("NOTION_API_KEY_ACTION_ITEMS not set.")
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def map_owner_to_notion_id(owner_name: Optional[str]) -> Optional[str]:
    if not owner_name:
        return None
    return PERSON_TO_NOTION_ID.get(owner_name.strip().lower())

def make_external_id(source_doc: str, paragraph_index: Optional[int], text: str) -> str:
    """
    Stable unique ID for de-duping.
    If paragraph_index is missing, still hash doc+text.
    """
    base = f"{source_doc}::{paragraph_index if paragraph_index is not None else 'NA'}::{text.strip()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def notion_query_by_external_id(database_id: str, external_id: str) -> Optional[str]:
    """
    Returns an existing Notion page_id if found, else None.
    Requires a DB property named `External ID` of type rich_text.
    """
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"
    payload = {
        "filter": {
            "property": PROP_EXTERNAL_ID,
            "rich_text": {"equals": external_id},
        }
    }
    r = requests.post(url, headers=notion_headers(), json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [])
    if results:
        return results[0]["id"]
    return None

def notion_create_page(database_id: str, properties: Dict) -> Dict:
    url = f"{NOTION_API_BASE}/pages"
    payload = {"parent": {"database_id": database_id}, "properties": properties}
    r = requests.post(url, headers=notion_headers(), json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Notion create failed: {r.status_code} {r.text}")
    return r.json()

def notion_update_page(page_id: str, properties: Dict) -> Dict:
    url = f"{NOTION_API_BASE}/pages/{page_id}"
    payload = {"properties": properties}
    r = requests.patch(url, headers=notion_headers(), json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Notion update failed: {r.status_code} {r.text}")
    return r.json()

def build_notion_properties(ai_item: Dict, source_doc: str, external_id: str) -> Dict:
    """
    Builds Notion properties for the DB row.
    Adjust these if your Notion DB property types differ.
    """
    name = ai_item.get("text") or "Action item"
    context = ai_item.get("context") or ""
    paragraph_index = ai_item.get("paragraph_index", None)

    # Status based on extracted status
    status_name = "Done" if ai_item.get("status") == "done" else DEFAULT_STATUS

    props: Dict = {
        PROP_NAME: {
            "title": [{"type": "text", "text": {"content": name}}]
        },
        PROP_STATUS: {
            "select": {"name": status_name}
        },
        PROP_CONTEXT: {
            "rich_text": [{"type": "text", "text": {"content": context}}]
        },
        PROP_EXTERNAL_ID: {
            "rich_text": [{"type": "text", "text": {"content": external_id}}]
        },
    }

    # Paragraph Index (number)
    if paragraph_index is not None:
        props[PROP_PARAGRAPH_INDEX] = {"number": float(paragraph_index)}

    # Due date
    due = ai_item.get("due_date")
    if due:
        parsed = dateparser.parse(str(due), settings={"PREFER_DATES_FROM": "future"})
        if parsed:
            props[PROP_DUE] = {"date": {"start": parsed.date().isoformat()}}
        else:
            props[PROP_DUE] = {"date": {"start": str(due)}}

    # Assignee
    notion_user_id = map_owner_to_notion_id(ai_item.get("owner"))
    if notion_user_id:
        props[PROP_ASSIGNEE] = {"people": [{"id": notion_user_id}]}

    return props

def upsert_action_item(database_id: str, ai_item: Dict, source_doc: str) -> str:
    """
    Create if not exists; update if exists.
    Returns page_id.
    """
    external_id = make_external_id(
        source_doc=source_doc,
        paragraph_index=ai_item.get("paragraph_index"),
        text=ai_item.get("text", ""),
    )

    existing_page_id = notion_query_by_external_id(database_id, external_id)
    props = build_notion_properties(ai_item, source_doc, external_id)

    if existing_page_id:
        notion_update_page(existing_page_id, props)
        return existing_page_id

    created = notion_create_page(database_id, props)
    return created["id"]


# =========================
# Orchestration
# =========================

def extract_action_items_from_single_docx(doc_path: str) -> List[Dict]:
    paragraphs = parse_docx(doc_path)
    joined = join_paragraphs(paragraphs)
    if not joined.strip():
        return []

    print(f"DEBUG: Extracted text from {doc_path}:\n{joined}")

    # extract_action_items_with_llm already returns List[Dict]
    action_items = extract_action_items_with_llm(joined)
    print("DEBUG: Parsed action items:", action_items)
    return action_items

def process_agenda_docs() -> None:
    if not NOTION_DATABASE_ID:
        raise RuntimeError("NOTION_DATABASE_ID_ACTION_ITEMS not set.")

    os.makedirs(RESULTS_FOLDER, exist_ok=True)

    docx_files = glob(os.path.join(AGENDA_FOLDER, "*.docx"))
    if not docx_files:
        print(f"No .docx found in: {AGENDA_FOLDER}")
        return

    all_results: Dict[str, List[Dict]] = {}

    created_count = 0
    updated_count = 0

    for doc_path in docx_files:
        filename = os.path.basename(doc_path)
        print(f"\nProcessing: {filename}")

        try:
            items = extract_action_items_from_single_docx(doc_path)
        except Exception as e:
            print(f"  ❌ Extraction failed for {filename}: {e}")
            all_results[filename] = []
            continue

        all_results[filename] = items
        print(f"  Extracted {len(items)} action items.")

        # Sync to Notion (upsert)
        for item in items:
            try:
                external_id = make_external_id(filename, item.get("paragraph_index"), item.get("text", ""))
                existing_page_id = notion_query_by_external_id(NOTION_DATABASE_ID, external_id)
                page_id = upsert_action_item(NOTION_DATABASE_ID, item, filename)
                if existing_page_id:
                    updated_count += 1
                    print(f"   ↻ Updated: {item.get('text')[:60]}... ({page_id})")
                else:
                    created_count += 1
                    print(f"   ✓ Created: {item.get('text')[:60]}... ({page_id})")
            except Exception as e:
                print(f"   ❌ Notion sync failed for item '{item.get('text','')[:40]}...': {e}")

    # Save results JSON
    output_path = os.path.join(RESULTS_FOLDER, "action_items.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    print(f"\nSaved extracted action items to: {output_path}")
    print(f"Notion sync summary: {created_count} created, {updated_count} updated.")

def flatten_action_items_json(json_path):
    """If action_items.json is a dict, flatten to a list."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        flat = []
        for v in data.values():
            if isinstance(v, list):
                flat.extend(v)
        return flat
    return data

def extract_action_items_from_agendas(output_json_path=None):
    """
    Extract action items from all agenda .docx files and write to output_json_path as a flat list.
    If output_json_path is None, defaults to dashboard's data file.
    """
    # Use dashboard's data file if not specified
    if output_json_path is None:
        output_json_path = (
            Path(__file__).parent.parent / "data" / "action_items.json"
        )
    else:
        output_json_path = Path(output_json_path)

    AGENDA_FOLDER = r"C:\Users\smulla1\Desktop\Personal Assistant\Email_Notion_Sync\Design-School-ASU---Directors-Personal-Assistant-Dashboard\src\agendas\agenda_documents"
    docx_files = glob(os.path.join(AGENDA_FOLDER, "*.docx"))
    all_items = []
    for doc_path in docx_files:
        items = extract_action_items_from_single_docx(doc_path)
        all_items.extend(items)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Extracted {len(all_items)} action items to {output_json_path}")


if __name__ == "__main__":
    # For CLI usage, also write to dashboard's data file for consistency
    extract_action_items_from_agendas()

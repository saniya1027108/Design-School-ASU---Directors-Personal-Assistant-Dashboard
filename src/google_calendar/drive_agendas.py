"""
Google Drive agendas: list an "Agendas" folder on Drive, read Google Docs inside
(subfolders = people or projects), extract action items via LLM, return by folder.
Uses the same OAuth token as Calendar (must include drive.readonly + documents.readonly).
Set GOOGLE_DRIVE_AGENDAS_FOLDER_ID in .env to the Drive folder ID of your root Agendas folder.
"""

import os
import sys
from pathlib import Path

# Ensure src is on path for agendas import
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv
load_dotenv()

# Same token as Calendar
from google_calendar.calendar_service import get_credentials

# Mime types
MIME_GOOGLE_DOC = "application/vnd.google-apps.document"
MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def get_drive_service():
    """Build Drive API service using shared OAuth credentials. Returns None if not connected."""
    creds = get_credentials()
    if not creds:
        return None
    try:
        from googleapiclient.discovery import build
        # cache_discovery=True avoids hanging on first request (no network fetch for discovery doc)
        return build("drive", "v3", credentials=creds, cache_discovery=True)
    except Exception:
        return None


def _get_folder_drive_id(folder_id, drive):
    """If the folder is in a Shared Drive, return its driveId; else return None. Raises on API errors."""
    print("[Drive extract] Getting folder metadata (driveId)...", flush=True)
    meta = drive.files().get(
        fileId=folder_id,
        fields="driveId",
        supportsAllDrives=True,
    ).execute()
    did = meta.get("driveId")
    print("[Drive extract] Folder driveId: %s" % (did or "My Drive"), flush=True)
    return did


def list_children(folder_id, drive=None):
    """List direct children (folders and files) of a Drive folder.
    Works for My Drive and Shared Drives. Returns list of {id, name, mimeType}.
    Raises on Drive API errors so callers can surface the real error.
    """
    drive = drive or get_drive_service()
    if not drive:
        return []
    q = f"'{folder_id}' in parents and trashed = false"
    params = {
        "q": q,
        "fields": "files(id,name,mimeType)",
        "pageSize": 200,
        "orderBy": "name",
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    # If folder is in a Shared Drive, we must use corpora=drive + driveId or the list is empty.
    drive_id = _get_folder_drive_id(folder_id, drive)
    if drive_id:
        params["corpora"] = "drive"
        params["driveId"] = drive_id
    print("[Drive extract] Listing children (files.list)...", flush=True)
    result = drive.files().list(**params).execute()
    files = result.get("files", [])
    print("[Drive extract] Listed %d children" % len(files), flush=True)
    return files


def _format_drive_error(e):
    """Turn Drive API exception into a short message for the UI."""
    try:
        from googleapiclient.errors import HttpError
        if isinstance(e, HttpError) and e.resp is not None:
            return f"{e.resp.status} {e.resp.reason}: {(getattr(e, 'reason') or str(e))[:200]}"
    except Exception:
        pass
    return str(e)[:200]


def get_doc_text(file_id, drive=None):
    """Export a Google Doc as plain text. Returns (text, error_str); error_str is None on success."""
    drive = drive or get_drive_service()
    if not drive:
        return (None, "Drive not connected")
    try:
        # files.export does not accept supportsAllDrives (only fileId and mimeType)
        data = drive.files().export(
            fileId=file_id,
            mimeType="text/plain",
        ).execute()
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace").strip()
        else:
            text = (data or "").strip()
        return (text if text else None, None)
    except Exception as e:
        return (None, _format_drive_error(e))


def get_docx_text(file_id, drive=None):
    """Download an uploaded .docx file and extract text. Returns (text, error_str)."""
    drive = drive or get_drive_service()
    if not drive:
        return (None, "Drive not connected")
    try:
        # get_media: some client versions don't accept supportsAllDrives; download works for Shared Drive by fileId
        request = drive.files().get_media(fileId=file_id)
        data = request.execute()
        if not data:
            return (None, "Empty file")
        try:
            from docx import Document
            from io import BytesIO
        except ImportError:
            return (None, "python-docx not installed")
        doc = Document(BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        return (text if text else None, None)
    except Exception as e:
        return (None, _format_drive_error(e))


def walk_agendas_and_extract(root_folder_id=None):
    """
    Walk 3-level agendas folder structure and extract action items from 2026 docs only.
    Structure: Root → Category folders (e.g. "Staff Meetings", "Projects") → Person/project folders → Docs
    Only docs with "2026" in the title are processed; "2025" docs are skipped.
    Returns:
      {
        "by_folder": {
          "Category Name": {
            "Person/Project Name": [
              { "doc_name": "...", "doc_id": "...", "doc_link": "...", "items": [...], "error": null or str }
            ],
            ...
          },
          ...
        },
        "all_items_flat": [ ... ]  # all items with source_category, source_folder, source_doc
      }
    """
    folder_id = root_folder_id or os.getenv("GOOGLE_DRIVE_AGENDAS_FOLDER_ID")
    if not folder_id:
        return {"by_folder": {}, "all_items_flat": [], "error": "GOOGLE_DRIVE_AGENDAS_FOLDER_ID not set"}

    print("[Drive extract] Getting Drive service...", flush=True)
    drive = get_drive_service()
    if not drive:
        return {"by_folder": {}, "all_items_flat": [], "error": "Google Drive not connected. Connect via Calendar/Drive."}
    print("[Drive extract] Drive service ready, listing root folder...", flush=True)

    try:
        from agendas.extract_and_sync import extract_action_items_from_notes_text
    except ImportError:
        return {"by_folder": {}, "all_items_flat": [], "error": "Could not import extraction function"}

    by_folder = {}
    all_items_flat = []

    try:
        # List direct children of root = category folders (e.g. "Staff Meetings", "Projects")
        children = list_children(folder_id, drive=drive)
    except Exception as e:
        err_msg = str(e)
        try:
            from googleapiclient.errors import HttpError
            if isinstance(e, HttpError) and e.resp is not None:
                err_msg = f"{e.resp.status} {e.resp.reason}: {getattr(e, 'reason', '') or err_msg}"
        except Exception:
            pass
        return {
            "by_folder": {},
            "all_items_flat": [],
            "error": f"Drive API error listing folder: {err_msg}. Check folder ID and that the connected Google account has access (including Shared Drives).",
        }

    def _is_archived_folder(name):
        """Skip **Archive / *Archive / Archived folders."""
        if not name:
            return False
        n = name.strip().lower()
        if "archive" not in n:
            return False
        if n.startswith("**") or n.startswith("*archive") or "**archive" in n:
            return True
        if n.startswith("*") and "archive" in n:
            return True
        return False

    category_folders = [c for c in children if c.get("mimeType") == MIME_FOLDER and not _is_archived_folder(c.get("name", ""))]
    print("[Drive extract] Root: %d category folders (archived excluded)" % len(category_folders), flush=True)

    def process_doc(file_id, doc_name, category_name, person_folder_name, mime_type=None):
        print("[Drive extract]     Reading doc: %s" % (doc_name[:50] + "..." if len(doc_name) > 50 else doc_name), flush=True)
        doc_link = f"https://docs.google.com/document/d/{file_id}/edit" if mime_type == MIME_GOOGLE_DOC else f"https://drive.google.com/file/d/{file_id}/view"
        if mime_type == MIME_DOCX:
            text, read_error = get_docx_text(file_id, drive=drive)
        else:
            text, read_error = get_doc_text(file_id, drive=drive)
        if not text:
            err = read_error or "Could not read document (empty or unsupported format)"
            print("[Drive extract]       -> failed: %s" % (err[:80]), flush=True)
            return {"doc_name": doc_name, "doc_id": file_id, "doc_link": doc_link, "items": [], "error": err}
        try:
            print("[Drive extract]       -> extracting action items (LLM)...", flush=True)
            items = extract_action_items_from_notes_text(text)
            print("[Drive extract]       -> got %d items" % len(items), flush=True)
        except Exception as e:
            return {"doc_name": doc_name, "doc_id": file_id, "doc_link": doc_link, "items": [], "error": str(e)}
        for it in items:
            it["source_category"] = category_name
            it["source_folder"] = person_folder_name
            it["source_doc"] = doc_name
            it["doc_link"] = doc_link
        return {"doc_name": doc_name, "doc_id": file_id, "doc_link": doc_link, "items": items, "error": None}

    # Walk 3 levels: Root → Category folders → (direct docs + person/project folders) → Docs (2026 only)
    for cat_idx, category in enumerate(category_folders):
        category_name = category.get("name", "Unnamed Category")
        print("[Drive extract] Category %d/%d: %s" % (cat_idx + 1, len(category_folders), category_name), flush=True)
        by_folder[category_name] = {}

        # List all children of this category folder (both subfolders AND docs directly in the category)
        category_children = list_children(category["id"], drive=drive)
        person_subfolders = [p for p in category_children if p.get("mimeType") == MIME_FOLDER]
        direct_docs = [c for c in category_children if c.get("mimeType") in (MIME_GOOGLE_DOC, MIME_DOCX)]

        # 1) Docs directly in the category folder (e.g. "2026 Sunny", "2026 Sandy" in "Dean's office 1:1")
        direct_docs_2026 = [d for d in direct_docs if "2026" in d.get("name", "") and "2025" not in d.get("name", "")]
        if direct_docs_2026:
            section_name = "This folder"
            print("[Drive extract]   Direct docs in category: %d (filtered for 2026: %d)" % (len(direct_docs), len(direct_docs_2026)), flush=True)
            by_folder[category_name][section_name] = []
            for doc in direct_docs_2026:
                mime = doc.get("mimeType") or MIME_GOOGLE_DOC
                out = process_doc(doc["id"], doc.get("name", "Untitled"), category_name, section_name, mime_type=mime)
                by_folder[category_name][section_name].append(out)
                all_items_flat.extend(out["items"])

        # 2) Person/project subfolders → docs inside each (2026 only); skip archived
        person_subfolders = [p for p in person_subfolders if not _is_archived_folder(p.get("name", ""))]
        for person_idx, person_folder in enumerate(person_subfolders):
            person_name = person_folder.get("name", "Unnamed")
            print("[Drive extract]   Person folder %d/%d: %s" % (person_idx + 1, len(person_subfolders), person_name), flush=True)
            by_folder[category_name][person_name] = []

            docs_children = list_children(person_folder["id"], drive=drive)
            docs_in_folder = [c for c in docs_children if c.get("mimeType") in (MIME_GOOGLE_DOC, MIME_DOCX)]
            docs_2026 = [d for d in docs_in_folder if "2026" in d.get("name", "") and "2025" not in d.get("name", "")]
            print("[Drive extract]     Found %d docs (filtered for 2026: %d)" % (len(docs_in_folder), len(docs_2026)), flush=True)

            for doc in docs_2026:
                mime = doc.get("mimeType") or MIME_GOOGLE_DOC
                out = process_doc(doc["id"], doc.get("name", "Untitled"), category_name, person_name, mime_type=mime)
                by_folder[category_name][person_name].append(out)
                all_items_flat.extend(out["items"])

    # Diagnostics
    total_docs = sum(
        len(docs)
        for category_dict in by_folder.values()
        for docs in category_dict.values()
    )
    return {
        "by_folder": by_folder,
        "all_items_flat": all_items_flat,
        "stats": {
            "category_folders": len(category_folders),
            "total_docs_processed": total_docs,
        },
    }

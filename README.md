# Directors Personal Assistant – Pipeline Documentation

This document describes the **entire pipeline** for the Design School ASU Directors Personal Assistant Dashboard: data sources, processing steps, storage, and the web interface under `src/`.

---

## Table of Contents

1. [Overview](#overview)
2. [Directory Structure](#directory-structure)
3. [Data Flow](#data-flow)
4. [Components](#components)
5. [Data Storage](#data-storage)
6. [Environment & Configuration](#environment--configuration)
7. [Running the Pipeline](#running-the-pipeline)
8. [Dashboard Routes Reference](#dashboard-routes-reference)

---

## Overview

The following diagram summarizes the pipeline: email (Outlook/Microsoft Graph), calendar (Google Calendar), and agendas/to-do (Google Drive → LLM extraction) feeding into the Personalized Dashboard and Kanban Board.

![Personalized Dashboard pipeline diagram](assets/personalized-dashboard.png)

The system is an **AI-powered executive assistant** that:

- **Syncs Outlook emails** into the dashboard and supports human-in-the-loop draft replies (LLM) and send-back via Microsoft Graph.
- **Extracts action items** from meeting agendas (local `.docx`, Google Docs, or Drive folder) using an LLM and stores them for the dashboard and optional Notion sync.
- **Shows Google Calendar** in a monthly view and can create events (OAuth).
- **Provides a Flask web dashboard** for emails, action items, agendas (meetings + Drive import), Kanban boards, and settings.

All persistent app data lives under `src/data/`. The pipeline can be run from the CLI (`pipeline/main.py`) or entirely through the dashboard (sync, extract, view).

---

## Directory Structure

```
src/
├── PIPELINE.md                 # This file
├── .env                        # Environment variables (secrets, API keys)
├── requirements.txt            # Python dependencies
│
├── dashboard/                  # Flask web app
│   ├── app.py                  # Main app: routes, auth, all integrations
│   └── templates/              # Jinja2 HTML (dashboard, emails, agendas, etc.)
│
├── data/                       # Runtime JSON storage (created by app)
│   ├── emails.json             # Synced Outlook emails
│   ├── action_items.json       # Extracted action items (flat or keyed by source)
│   ├── drafts.json             # Generated reply drafts
│   ├── meetings.json           # Agendas/meetings (notes + action items)
│   ├── boards.json             # Kanban boards
│   ├── drive_agendas.json      # Last Drive import result (by_folder, stats)
│   ├── user_settings.json      # Display name, email
│   └── google_calendar_token.json  # OAuth token (Calendar + Drive)
│
├── pipeline/                   # CLI orchestrator
│   └── main.py                 # Menu: sync emails, extract action items, run dashboard
│
├── outlook/                    # Microsoft Outlook / Graph integration
│   ├── config/                 # organization_chart.json, keywords.json, token cache
│   ├── main/                   # Alternative Flask app entry (outlook-only)
│   ├── sync/                   # Sync, read, draft, revise, send
│   └── utils/                  # Auth (MSAL), Notion helpers
│
├── agendas/                    # Action-item extraction from agendas
│   ├── extract_and_sync.py     # LLM extraction, .docx parse, Notion upsert, Google Doc helpers
│   ├── agenda_documents/       # Local .docx sample/input
│   └── results/                # Optional output (e.g. action_items.json copy)
│
└── google_calendar/            # Google Calendar + Drive
    ├── calendar_service.py     # OAuth, fetch events, create event
    ├── drive_agendas.py        # Walk Drive folder, read Docs, extract action items (LLM)
    ├── README.md               # Calendar setup
    └── DRIVE_AGENDAS_SETUP.md  # Drive Agendas folder setup
```

---

## Data Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Microsoft      │     │  Outlook sync    │     │  src/data/      │
│  Graph (Mail)   │────▶│  sync_outlook_   │────▶│  emails.json    │
│                 │     │  json.py         │     │  drafts.json    │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
        │                            │                      │
        │                            │                      ▼
        │                            │             ┌─────────────────┐
        │                            │             │  Dashboard      │
        │                            │             │  (Flask)        │
        │                            │             │  /emails_full   │
        │                            │             │  /drafts/*      │
        │                            │             └────────┬────────┘
        │                            │                      │
┌───────┴───────────┐                │                      │
│  Local .docx      │                │                      │
│  agendas/         │                │                      │
└────────┬──────────┘                │                      │
        │                            │                      │
        │   ┌───────────────────────┘                      │
        │   │                                                │
        ▼   ▼                                                │
┌─────────────────────┐     ┌──────────────────┐     ┌───────┴────────┐
│  Google Drive       │     │  agendas/        │     │  action_items  │
│  (Agendas folder)   │────▶│  extract_and_    │────▶│  .json        │
│  Google Docs        │     │  sync.py         │     │  (optional    │
└─────────────────────┘     │  drive_agendas  │     │   Notion)     │
        │                    └──────────────────┘     └───────┬───────┘
        │                              │                       │
        │                              ▼                       ▼
        │                    ┌──────────────────┐     ┌─────────────────┐
        │                    │  LLM (OpenAI-    │     │  Dashboard      │
        │                    │  compatible)     │     │  /, /action_    │
        │                    │  action item     │     │  items_full     │
        │                    │  extraction      │     └─────────────────┘
        │                    └──────────────────┘
        │
        ▼
┌─────────────────┐     ┌──────────────────┐
│  Google         │     │  calendar_       │
│  Calendar API   │────▶│  service.py      │────▶ Dashboard calendar view
└─────────────────┘     └──────────────────┘     + create event
```

- **Emails:** Outlook (Graph) → `sync_outlook_json` → `emails.json`; drafts and send via dashboard.
- **Action items:** Local `.docx` (agendas folder), Google Docs, or Drive Agendas folder → LLM extraction → `action_items.json` (and optionally Notion). Dashboard shows top 10 to-do / 10 done on home, full list on `/action_items_full`.
- **Calendar:** Google OAuth → `calendar_service` → monthly view and create event in dashboard.
- **Drive Agendas:** Same Google OAuth; `drive_agendas.py` walks folder structure, reads Docs, runs same LLM extraction, merges into `action_items.json` and stores last result in `drive_agendas.json` for the Agendas UI.

---

## Components

### 1. Dashboard (`dashboard/app.py`)

- **Flask app** with session-based login (username only; no password check in default flow).
- **Templates:** `base.html`, `dashboard.html`, `emails_full.html`, `action_items_full.html`, `agendas_list.html`, `agenda_detail.html`, `agenda_action_items.html`, `kanban.html`, `settings.html`, `login.html`.
- **Responsibilities:**
  - Serve main dashboard (calendar, stats, top 10 to-do/done, quick actions).
  - Emails: list, update reply instructions, generate/revise/send drafts.
  - Action items: list, mark done, full page; dashboard list sorted by due date, shows folder (source), assignee (owner), due date.
  - Agendas: meetings list, meeting detail with notes and action items, extract from notes, link to Drive doc action-items page; Drive import (extract from folder, merge into action items).
  - Google Calendar: connect OAuth, fetch events, create event.
  - Google Docs OAuth: connect, list docs, extract single doc or folder to action items.
  - Kanban: CRUD boards and columns.
  - Settings: user display name and email.

### 2. Pipeline CLI (`pipeline/main.py`)

- **Menu-driven:** (1) Sync emails to JSON, (2) Extract action items from agendas (.docx), (3) Start dashboard, (4) Exit.
- **Environment:** `--env dev` (`.env`) or `--env director` (`.env.director`).
- **Uses:** `outlook.sync.sync_outlook_json.sync_emails_to_json`, `agendas.extract_and_sync.extract_action_items_from_agendas`, `dashboard.app.run_dashboard`.

### 3. Outlook (`outlook/`)

- **Auth:** MSAL device-code or interactive flow; tokens in `config/outlook_token_cache.json`.
- **sync_outlook_json.py:** Fetches unread (or recent) emails via Graph, normalizes to dashboard format, writes `emails.json`.
- **outlook_read.py:** Fetch single message, list; uses `organization_chart.json` for sender display name and category.
- **draft_replies.py:** Generate draft reply using LLM (instructions from dashboard/Notion).
- **revise_drafts.py:** Revise existing draft.
- **send_approved_replies.py:** Send email via Graph and update status.
- **sync_outlook_notion.py / reply_outlook_notion.py:** Notion sync and reply workflow (if used).

### 4. Agendas & Action Items (`agendas/extract_and_sync.py`)

- **Inputs:**
  - Local `.docx` in `agenda_documents/` (parsed with `python-docx`; DONE/TODO section detection).
  - Raw meeting notes text (e.g. from dashboard “Extract from notes”).
  - Google Doc text (fetched via Drive/Docs API; service account or OAuth).
- **LLM:** OpenAI-compatible API; extracts structured action items (text, owner, due_date, priority, status, context).
- **Output:** List of dicts written to `action_items.json` (or passed to callers). Drive flow also adds `source_category`, `source_folder`, `source_doc`, `doc_link`.
- **Notion:** Optional upsert to Notion DB using External ID for deduplication; uses `NOTION_API_KEY_ACTION_ITEMS`, `NOTION_DATABASE_ID_ACTION_ITEMS`.

### 5. Google Calendar (`google_calendar/`)

- **calendar_service.py:** OAuth flow, token storage in `data/google_calendar_token.json`, fetch events for month, create event. Used by dashboard for calendar view and “Add event.”
- **drive_agendas.py:** Uses same OAuth. Walks root Agendas folder (from `GOOGLE_DRIVE_AGENDAS_FOLDER_ID`): categories → person/project subfolders → 2026 Google Docs; exports text, runs LLM extraction per doc; returns `by_folder` and flat list; dashboard can merge flat list into `action_items.json`.

### 6. Data Handling in Dashboard

- **action_items.json:** Loaded and flattened (if stored as dict keyed by source) via `_flatten_action_items`. Dashboard home shows up to 10 todo + 10 done, sorted by due date (earliest first; no-date last). Full list and “mark done” on `/action_items_full`.
- **drive_agendas.json:** Last Drive import result (by_folder, last_import, stats) for Agendas tab.
- **meetings.json:** List of meetings (title, date, attendees, notes, action_items); used by Agendas list and detail pages.

---

## Data Storage

| File | Purpose |
|------|--------|
| `data/emails.json` | List of emails from Outlook sync (id, from, subject, email, summary, category, date, reply_instruction, draft_status, etc.). |
| `data/action_items.json` | Flat list (or dict flattened on load) of action items: text, status, due_date, owner, source_folder/source_category/source, etc. |
| `data/drafts.json` | Generated email drafts for dashboard. |
| `data/meetings.json` | Agendas/meetings with notes and action_items. |
| `data/boards.json` | Kanban boards and columns. |
| `data/drive_agendas.json` | Last Drive import: by_folder, last_import, folder_id, stats. |
| `data/user_settings.json` | User display name and email. |
| `data/google_calendar_token.json` | Google OAuth token (Calendar + Drive). |
| `outlook/config/outlook_token_cache.json` | Microsoft Graph token cache. |

---

## Environment & Configuration

Configure via `src/.env` (and optionally `.env.director` for pipeline CLI).

| Variable | Purpose |
|----------|---------|
| **Outlook** | `OUTLOOK_CLIENT_ID`, `OUTLOOK_CLIENT_SECRET`, `OUTLOOK_TENANT_ID`, `OUTLOOK_USER`, `OUTLOOK_FETCH_TOP` |
| **Notion** | `NOTION_API_KEY`, `NOTION_DATABASE_ID` (emails); `NOTION_API_KEY_ACTION_ITEMS`, `NOTION_DATABASE_ID_ACTION_ITEMS` (action items); optional `PERSON_TO_NOTION_ID` |
| **LLM** | `OPENAI_API_KEY`, `OPENAI_API_BASE`, `OPENAI_MODEL` (e.g. gpt-4o-mini); `GROQ_API_KEY` if used |
| **Google** | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_CALENDAR_ID`, `GOOGLE_REDIRECT_URI_CALENDAR`; `GOOGLE_DRIVE_AGENDAS_FOLDER_ID` for Drive Agendas; optional service account path for Docs |
| **App** | `FLASK_SECRET_KEY` for session |

See `google_calendar/README.md` and `google_calendar/DRIVE_AGENDAS_SETUP.md` for Calendar and Drive setup.

---

## Running the Pipeline

1. **Install dependencies (from project or `src/`):**
   ```bash
   pip install -r src/requirements.txt
   ```

2. **Configure `src/.env`** with Outlook, Notion, OpenAI, and Google credentials as needed.

3. **Option A – CLI (pipeline menu):**
   ```bash
   cd src
   python pipeline/main.py
   ```
   Then choose: 1 = Sync emails, 2 = Extract action items from .docx, 3 = Start dashboard, 4 = Exit. Use `--env director` to load `.env.director`.

4. **Option B – Dashboard only:**
   ```bash
   cd src
   python -m dashboard.app
   ```
   Opens at `http://127.0.0.1:5000/`. Log in, then use “Sync Emails,” “Extract from docx (legacy),” or “Import from Drive” on Agendas as needed.

5. **First-time Google:** In dashboard, use “Connect Google Calendar” (and if using Drive, ensure Drive scope is requested and re-authorize once). Token is stored in `data/google_calendar_token.json`.

---

## Dashboard Routes Reference

| Route | Method | Description |
|-------|--------|-------------|
| `/login`, `/logout` | GET/POST, GET | Session login (username), logout |
| `/` | GET | Main dashboard (calendar, stats, 10 to-do, 10 done, quick actions) |
| `/emails_full` | GET | Full email list |
| `/sync_emails` | POST | Sync Outlook → emails.json |
| `/emails/update` | POST | Update reply instructions etc. |
| `/drafts/generate`, `/drafts/revise`, `/drafts/send` | POST | Draft lifecycle |
| `/action_items_full` | GET | All action items (grouped todo/done) |
| `/action_items/mark_done` | POST | Mark item done |
| `/extract_action_items` | POST | Extract from .docx (legacy) → action_items.json |
| `/agendas` | GET | Redirect to agendas list |
| `/agendas/<meeting_id>` | GET | Meeting detail (notes, action items) |
| `/agendas/drive/action-items/<doc_id>` | GET | Full-page action items for one Drive doc |
| `/api/meetings`, `/api/meetings/<id>` | GET/POST, GET/PUT | Meetings CRUD |
| `/api/meetings/<id>/extract_action_items` | POST | Extract action items from meeting notes |
| `/api/drive/agendas/extract` | POST | Drive import: extract from folder, optional merge to action_items |
| `/calendar/connect`, `/calendar/oauth2callback` | GET | Google Calendar OAuth |
| `/api/calendar/events` | GET, POST | List events, create event |
| `/google_docs/connect`, `/google_docs/oauth2callback` | GET | Google Docs OAuth |
| `/api/google_docs/list`, `/api/google_docs/extract_doc`, `extract_folder` | GET, POST | List docs, extract one doc or folder |
| `/kanban` | GET | Kanban boards |
| `/api/boards`, `/api/boards/<id>` | GET/POST, GET/PUT | Boards CRUD |
| `/settings`, `/api/settings` | GET, POST | User settings |
S
---

This file is the single markdown reference for the entire pipeline under `src/`. For Google Calendar and Drive Agendas setup details, see `google_calendar/README.md` and `google_calendar/DRIVE_AGENDAS_SETUP.md`.

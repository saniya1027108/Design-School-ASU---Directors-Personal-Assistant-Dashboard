# Google Drive Agendas – Step-by-Step Setup

This lets the dashboard **extract action items from Google Docs** in a Drive folder, **organized by subfolders** (e.g. one subfolder per staff person or project).

---

## 1. Enable the Google Drive API

1. Open **Google Cloud Console**: [https://console.cloud.google.com](https://console.cloud.google.com)
2. Select the **same project** you use for Calendar (the one with your OAuth client ID).
3. Go to **APIs & Services → Library**.
4. Search for **Google Drive API** and open it.
5. Click **Enable**.

No need to enable “Google Docs API” separately for reading Doc content; the Drive API can export Docs as text.

---

## 2. Add Drive and Docs scopes to your OAuth client

The app already requests these scopes in code:

- `https://www.googleapis.com/auth/drive.readonly` – list folders/files and export Docs
- `https://www.googleapis.com/auth/documents.readonly` – (optional) read Doc structure

So you **don’t add scopes in the Console by hand**. When the user signs in, the app will request Calendar + Drive + Docs access. You only need to **re-authorize** once so the token includes the new scopes (see step 4).

---

## 3. Create the Agendas folder structure on Google Drive

1. In **Google Drive**, create a **root folder** (e.g. **Agendas**).
2. Inside it, create **subfolders** for each person or project, for example:
   - `Agendas/Mireille/`
   - `Agendas/2026 DP/`
   - `Agendas/Project Alpha/`
3. Inside each subfolder, add **Google Docs** (not uploaded .docx) that contain meeting notes or agendas.

Only **Google Docs** (native Docs) are read; uploaded Word files are not processed.

**Get the root folder ID:**

1. In Drive, open the **root Agendas folder** (the one that contains the subfolders).
2. Look at the URL:  
   `https://drive.google.com/drive/folders/`**`1ABC...xyz`**  
   The part after `/folders/` is the **folder ID**.

---

## 4. Configure the app and re-connect Google

1. **Environment variables** (in `src/.env`):

   ```env
   GOOGLE_DRIVE_AGENDAS_FOLDER_ID=YOUR_ROOT_AGENDAS_FOLDER_ID
   ```

   Replace `YOUR_ROOT_AGENDAS_FOLDER_ID` with the folder ID from step 3 (e.g. `1ABC...xyz`).

   Your existing `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are used for both Calendar and Drive.

2. **Re-authorize** so the token includes Drive/Docs:

   - Delete the existing token file:  
     `src/data/google_calendar_token.json`
   - In the dashboard, click **Connect Google (Calendar + Drive)** (on the Agendas page) or use the Calendar connect flow.
   - Sign in and **allow** access to Calendar and to **Google Drive** (and Docs if asked).
   - After that, the same token is used for Calendar and for Drive agendas.

---

## 5. Use “Import from Drive” on the dashboard

1. Go to **Agendas** in the dashboard.
2. Click **Import from Drive folder…**.
3. **Optional:** Enter a **Drive folder ID** if you want to use a different folder than the one in `.env`. Otherwise leave it empty to use `GOOGLE_DRIVE_AGENDAS_FOLDER_ID`.
4. Check **Add extracted action items to Action Items dashboard** if you want them merged into the main action items list.
5. Click **Extract from Drive**.

The app will:

- List **subfolders** of the root Agendas folder (each = person or project).
- In each subfolder, list **Google Docs**.
- For each Doc, **export the text** and run the **LLM extraction** to get action items.
- Show results **grouped by folder** and, if you chose it, add all items to the Action Items dashboard.

---

## Summary checklist

| Step | Action |
|------|--------|
| 1 | Enable **Google Drive API** in Cloud Console (same project as Calendar). |
| 2 | (Scopes are in code; no extra Console scope config needed.) |
| 3 | Create **Agendas** folder on Drive, add **subfolders** (people/projects), put **Google Docs** inside. Copy the **root folder ID** from the URL. |
| 4 | Set **GOOGLE_DRIVE_AGENDAS_FOLDER_ID** in `.env`. Delete `google_calendar_token.json` and **re-connect** Google (Calendar + Drive) once. |
| 5 | On **Agendas** page, use **Import from Drive folder…** and run **Extract from Drive**. |

You can still use the **note-taking tool** on the Agendas page for meetings that are not in Drive; Drive import is an additional source that follows your folder structure.

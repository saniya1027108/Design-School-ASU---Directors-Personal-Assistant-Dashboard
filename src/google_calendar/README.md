# Google Calendar integration

The dashboard can show a monthly calendar view with events from your Google Calendar.

## Setup

1. **Google Cloud Console**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create or select a project
   - Enable **Google Calendar API** (APIs & Services → Library → search "Google Calendar API")
   - Go to **APIs & Services → Credentials**
   - Create **OAuth 2.0 Client ID** (Application type: Web application or Desktop)
   - Add **Authorized redirect URI**: `http://127.0.0.1:5000/calendar/oauth2callback` (or your app URL + `/calendar/oauth2callback`)
   - Copy the **Client ID** and **Client secret**

2. **Environment variables** (in `src/.env`)
   ```
   GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your_client_secret
   ```
   Optional:
   - `GOOGLE_CALENDAR_ID=primary` (default; use "primary" for your main calendar)
   - `GOOGLE_REDIRECT_URI=http://127.0.0.1:5000/calendar/oauth2callback` (if different from default)

3. **Connect in the dashboard**
   - Open the dashboard; you’ll see a “Connect Google Calendar” button in the Calendar section
   - Click it, sign in with Google, and allow calendar read access
   - You’ll be redirected back and the monthly view will show your events

Token is stored in `src/data/google_calendar_token.json` and reused until you revoke access or delete the file.

**Adding events:** The app can also create events (scope includes `calendar.events`). If you previously connected with read-only scope, delete `src/data/google_calendar_token.json` and click "Connect Google Calendar" again to grant the new permission.

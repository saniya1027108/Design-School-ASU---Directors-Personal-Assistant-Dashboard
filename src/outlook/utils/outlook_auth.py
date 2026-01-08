# outlook_auth.py
import sys
from pathlib import Path
import json
import os
import time
import requests
from dotenv import load_dotenv

# ...existing code for sys.path, etc...

load_dotenv()

USER = os.getenv("OUTLOOK_USER")
CLIENT_ID = os.getenv("OUTLOOK_CLIENT_ID") or "26895341-d03d-4265-a82b-ed2e66508294"  # fallback to your client id
TOKEN_CACHE_PATH = Path(__file__).parent.parent / "config" / "outlook_token_cache.json"
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPE = ["Mail.Read", "Mail.ReadWrite", "Mail.Send", "User.Read", "email", "openid", "profile"]

def load_token_cache():
    if TOKEN_CACHE_PATH.exists():
        with open(TOKEN_CACHE_PATH, "r") as f:
            return json.load(f)
    return {}

def save_token_cache(cache):
    with open(TOKEN_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=4)

def get_cached_access_token():
    cache = load_token_cache()
    access_tokens = cache.get("AccessToken", {})
    now = int(time.time())
    for token_data in access_tokens.values():
        expires_on = int(token_data.get("expires_on", "0"))
        if expires_on > now + 60:  # 60s buffer
            return token_data["secret"]
    return None

def get_refresh_token():
    cache = load_token_cache()
    refresh_tokens = cache.get("RefreshToken", {})
    for token_data in refresh_tokens.values():
        return token_data["secret"]
    return None

def refresh_access_token(refresh_token):
    data = {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": " ".join(SCOPE),
    }
    resp = requests.post(f"{AUTHORITY}/oauth2/v2.0/token", data=data)
    if resp.status_code == 200:
        token_response = resp.json()
        # Update cache
        cache = load_token_cache()
        now = int(time.time())
        access_token = token_response["access_token"]
        expires_in = int(token_response["expires_in"])
        expires_on = now + expires_in
        # Save new access token
        cache.setdefault("AccessToken", {})
        cache["AccessToken"]["latest"] = {
            "credential_type": "AccessToken",
            "secret": access_token,
            "expires_on": str(expires_on)
        }
        # Save new refresh token if present
        if "refresh_token" in token_response:
            cache.setdefault("RefreshToken", {})
            cache["RefreshToken"]["latest"] = {
                "credential_type": "RefreshToken",
                "secret": token_response["refresh_token"]
            }
        save_token_cache(cache)
        return access_token
    else:
        return None

def device_code_auth():
    data = {
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPE)
    }
    resp = requests.post(f"{AUTHORITY}/oauth2/v2.0/devicecode", data=data)
    resp.raise_for_status()
    device_code_info = resp.json()
    print(f"To authenticate, visit {device_code_info['verification_uri']} and enter code: {device_code_info['user_code']}")
    # Poll for token
    while True:
        time.sleep(device_code_info["interval"])
        poll_data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID,
            "device_code": device_code_info["device_code"]
        }
        poll_resp = requests.post(f"{AUTHORITY}/oauth2/v2.0/token", data=poll_data)
        if poll_resp.status_code == 200:
            token_response = poll_resp.json()
            # Save to cache
            cache = load_token_cache()
            now = int(time.time())
            access_token = token_response["access_token"]
            expires_in = int(token_response["expires_in"])
            expires_on = now + expires_in
            cache.setdefault("AccessToken", {})
            cache["AccessToken"]["latest"] = {
                "credential_type": "AccessToken",
                "secret": access_token,
                "expires_on": str(expires_on)
            }
            if "refresh_token" in token_response:
                cache.setdefault("RefreshToken", {})
                cache["RefreshToken"]["latest"] = {
                    "credential_type": "RefreshToken",
                    "secret": token_response["refresh_token"]
                }
            save_token_cache(cache)
            return access_token
        elif poll_resp.status_code in (400, 401):
            error = poll_resp.json().get("error")
            if error == "authorization_pending":
                continue
            else:
                raise Exception(f"Device code auth failed: {poll_resp.text}")
        else:
            raise Exception(f"Device code auth failed: {poll_resp.text}")

def get_token():
    # 1. Try cached access token
    token = get_cached_access_token()
    if token:
        return token
    # 2. Try refresh token
    refresh_token = get_refresh_token()
    if refresh_token:
        token = refresh_access_token(refresh_token)
        if token:
            return token
    # 3. Prompt for device code authentication
    return device_code_auth()

# ...existing code for category/priority, fetch_unread_emails, etc...
# outlook_auth.py
import msal
import os
import json
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("OUTLOOK_CLIENT_ID")
CLIENT_SECRET = os.getenv("OUTLOOK_CLIENT_SECRET")
TENANT_ID = os.getenv("OUTLOOK_TENANT_ID")

AUTHORITY = "https://login.microsoftonline.com/common"

SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
    "Mail.Send"
]

# Store the token cache in the src/outlook/config folder
current_file = os.path.abspath(__file__)
TOKEN_CACHE_FILE = os.path.abspath(os.path.join(current_file, "../../config/outlook_token_cache.json"))


def load_cache():
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, "r") as f:
                data = json.load(f)
                cache = msal.SerializableTokenCache()
                cache.deserialize(json.dumps(data))
                return cache
        except Exception as e:
            print(f"⚠️ Could not load cache: {e}")
    return msal.SerializableTokenCache()


def save_cache(cache):
    if cache.has_state_changed:
        with open(TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())


def get_token():
    cache = load_cache()

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            print("🔐 Token acquired silently")
            save_cache(cache)
            return result["access_token"]

    print("🔑 Starting interactive authentication...")
    flow = app.initiate_device_flow(scopes=SCOPES)

    if "user_code" not in flow:
        raise ValueError("Device flow failed to start")

    print(f"\n👉 Go to: {flow['verification_uri']}")
    print(f"👉 Enter this code: {flow['user_code']}\n")
    print("⏳ Waiting for authentication...")

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise Exception("Login failed: " + str(result.get("error_description", result)))

    print("🎉 Authentication successful!")
    save_cache(cache)

    return result["access_token"]


if __name__ == "__main__":
    token = get_token()
    print("✅ Token acquired successfully")
    print(f"Token preview: {token[:60]}...")
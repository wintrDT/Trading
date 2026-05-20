"""
One-time script to get your Tastytrade refresh token via OAuth.

Usage:
  python -m bot.auth
"""
import asyncio
import base64
import hashlib
import os
import webbrowser
from urllib.parse import urlencode

import httpx

AUTH_ENDPOINT = "https://my.tastytrade.com/auth.html"
TOKEN_ENDPOINT = "https://api.tastytrade.com/oauth/token"


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def main():
    print("Tastytrade Token Generator")
    print("--------------------------\n")

    client_id = input("Client ID: ").strip()
    client_secret = input("Client secret (TT_SECRET): ").strip()
    redirect_uri = input("Redirect URI (http://localhost): ").strip() or "http://localhost"

    verifier, challenge = _pkce_pair()

    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "read trade openid offline_access",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTH_ENDPOINT}?{params}"

    print(f"\nOpening browser for Tastytrade login...")
    print(f"(If it doesn't open: {auth_url})\n")
    webbrowser.open(auth_url)

    print("After you log in and approve, your browser will redirect to:")
    print("  http://localhost?code=XXXXXXXX\n")
    print("The page will look broken — that's fine.")
    print("Copy the full URL from the address bar and paste it below.\n")

    pasted = input("Paste the redirect URL (or just the code): ").strip()
    if "code=" in pasted:
        code = pasted.split("code=")[1].split("&")[0]
    else:
        code = pasted

    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_ENDPOINT, json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        })

    if resp.status_code != 200:
        print(f"\nFailed ({resp.status_code}):")
        print(resp.text)
        return

    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print("\nNo refresh_token in response:")
        print(data)
        return

    print("\nSuccess! Paste this into your .env:\n")
    print(f"TT_REFRESH={refresh_token}")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
One-time Spotify OAuth setup.

Run this script once from a terminal on the Pi to authenticate with the
Spotify Web API. After completing the flow, a token is cached and the
main app can make API calls without any browser.

Setup:
1. Go to https://developer.spotify.com/dashboard and create an app
2. Add 'http://localhost:8888/callback' as a Redirect URI in the app settings
3. Copy your Client ID and Client Secret into config.py (or set as env vars)
4. Run: python scripts/spotify_auth.py
5. Visit the URL it prints, authorize the app, then paste the redirect URL back
"""

import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_CACHE_PATH,
)

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    print("spotipy not installed. Run: pip install spotipy")
    sys.exit(1)

SCOPES = (
    "user-modify-playback-state user-read-playback-state "
    "user-library-read user-read-recently-played "
    "playlist-read-private playlist-read-collaborative"
)


def main():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        print("ERROR: SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not configured.")
        print("Set them in config.py or as environment variables.")
        sys.exit(1)

    print(f"Using redirect URI: {SPOTIFY_REDIRECT_URI}")
    print(f"Token will be cached to: {SPOTIFY_CACHE_PATH}")
    print()

    auth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPES,
        cache_path=SPOTIFY_CACHE_PATH,
        open_browser=False,
    )

    # Check if we already have a valid token
    token_info = auth.get_cached_token()
    if token_info and not auth.is_token_expired(token_info):
        print("Already authenticated! Token is valid.")
        sp = spotipy.Spotify(auth=token_info["access_token"])
        me = sp.me()
        print(f"Logged in as: {me['display_name']} ({me['email']})")
        print()
        print("Spotify controls are ready. You can now run the main app.")
        return

    # Start OAuth flow
    auth_url = auth.get_authorize_url()
    print("=" * 60)
    print("STEP 1: Open this URL in your browser (on any device):")
    print()
    print(auth_url)
    print()
    print("=" * 60)
    print("STEP 2: Authorize the app in Spotify.")
    print("STEP 3: You will be redirected to localhost:8888/callback")
    print("        (the page will fail to load - that's OK)")
    print("STEP 4: Copy the full URL from your browser's address bar")
    print("        and paste it below.")
    print()
    redirect_response = input("Paste redirect URL here: ").strip()

    try:
        code = auth.parse_response_code(redirect_response)
        token_info = auth.get_access_token(code, as_dict=False)
        sp = spotipy.Spotify(auth=token_info)
        me = sp.me()
        print()
        print(f"Authenticated as: {me['display_name']} ({me.get('email', '')})")
        print(f"Token cached to: {SPOTIFY_CACHE_PATH}")
        print()
        print("Done! Spotify controls are now enabled.")
    except Exception as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

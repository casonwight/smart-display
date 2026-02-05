#!/usr/bin/env python3
"""
Librespot onevent callback script.
Writes current playback state to a JSON file for the MusicApp to read.
"""

import os
import json
from datetime import datetime
from pathlib import Path

# State file location
STATE_FILE = Path("/tmp/spotify_state.json")


def main():
    event = os.environ.get("PLAYER_EVENT", "")

    # Read existing state or create new
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (json.JSONDecodeError, IOError):
        state = {}

    # Update timestamp
    state["last_update"] = datetime.now().isoformat()
    state["event"] = event

    if event == "track_changed":
        # Full track metadata available
        # COVERS is a newline-separated list of URLs (different sizes)
        covers_raw = os.environ.get("COVERS", "")
        # Take the first (usually largest) cover URL
        cover_url = covers_raw.split("\n")[0].strip() if covers_raw else ""

        state["track"] = {
            "name": os.environ.get("NAME", "Unknown"),
            "artists": os.environ.get("ARTISTS", "Unknown"),
            "album": os.environ.get("ALBUM", "Unknown"),
            "duration_ms": int(os.environ.get("DURATION_MS", 0)),
            "uri": os.environ.get("URI", ""),
            "cover_url": cover_url,
            "is_explicit": os.environ.get("IS_EXPLICIT", "false") == "true",
        }
        state["is_playing"] = True
        state["position_ms"] = 0

    elif event == "playing":
        state["is_playing"] = True
        state["position_ms"] = int(os.environ.get("POSITION_MS", 0))

    elif event == "paused":
        state["is_playing"] = False
        state["position_ms"] = int(os.environ.get("POSITION_MS", 0))

    elif event == "stopped":
        state["is_playing"] = False
        state["position_ms"] = 0

    elif event == "seeked":
        state["position_ms"] = int(os.environ.get("POSITION_MS", 0))

    elif event == "volume_changed":
        # Volume is 0-65535
        volume_raw = int(os.environ.get("VOLUME", 0))
        state["volume"] = round(volume_raw / 65535 * 100)

    elif event == "shuffle_changed":
        state["shuffle"] = os.environ.get("SHUFFLE", "false").lower() == "true"

    elif event == "repeat_changed":
        state["repeat"] = os.environ.get("REPEAT", "false").lower() == "true"

    elif event in ("session_connected", "session_disconnected"):
        state["connected"] = event == "session_connected"
        if event == "session_connected":
            state["user"] = os.environ.get("USER_NAME", "")

    # Write state atomically
    temp_file = STATE_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(state, indent=2))
    temp_file.rename(STATE_FILE)


if __name__ == "__main__":
    main()

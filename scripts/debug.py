#!/usr/bin/env python3
"""
Smart Display Debug Tool - headless testing & state inspection.

Usage:
    python scripts/debug.py screenshot [screen]   # Render a screen to PNG
    python scripts/debug.py spotify               # Check Spotify API state
    python scripts/debug.py state                 # Read /tmp/spotify_state.json
    python scripts/debug.py simulate playlist     # Simulate playlist selection flow
    python scripts/debug.py screens               # Render all screens to /tmp/
    python scripts/debug.py logs [lines]          # Tail the app log (if running via systemd)
    python scripts/debug.py running               # Check if main app is running
"""

import sys
import os
import json
import subprocess
import time

# Must be first - mock hardware before project imports
from unittest.mock import MagicMock

_epd_mock = MagicMock()
sys.modules["waveshare_epd"] = _epd_mock
sys.modules["waveshare_epd.epd7in5_V2"] = _epd_mock
sys.modules["gpiozero"] = MagicMock()
sys.modules["pvporcupine"] = MagicMock()
sys.modules["pvrhino"] = MagicMock()
sys.modules["sounddevice"] = MagicMock()
sys.modules["faster_whisper"] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cmd_screenshot(screen: str = "all"):
    """Render screen(s) to PNG."""
    script = os.path.join(os.path.dirname(__file__), "screenshot.py")
    result = subprocess.run(
        [sys.executable, script, screen],
        capture_output=True, text=True
    )
    print(result.stdout.strip())
    if result.stderr:
        print("STDERR:", result.stderr[:500])


def cmd_spotify():
    """Check Spotify API state - devices, current track."""
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        print("spotipy not installed")
        return

    from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI, SPOTIFY_CACHE_PATH

    if not SPOTIFY_CLIENT_ID:
        print("No Spotify credentials in config")
        return

    try:
        auth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-modify-playback-state user-read-playback-state user-library-read user-read-recently-played playlist-read-private playlist-read-collaborative",
            cache_path=SPOTIFY_CACHE_PATH,
            open_browser=False,
        )
        token = auth.get_cached_token()
        if not token:
            print("No cached Spotify token - run: python scripts/spotify_auth.py")
            return

        sp = spotipy.Spotify(auth_manager=auth)

        print("=== Spotify Devices ===")
        devices = sp.devices()
        for d in devices.get("devices", []):
            active = "ACTIVE" if d.get("is_active") else "inactive"
            print(f"  [{active}] {d.get('name')} (id={d.get('id', '')[:12]}…) vol={d.get('volume_percent')}%")
        if not devices.get("devices"):
            print("  (no devices found)")

        print("\n=== Current Playback ===")
        playback = sp.current_playback()
        if playback and playback.get("item"):
            item = playback["item"]
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            progress = playback.get("progress_ms", 0) // 1000
            duration = item.get("duration_ms", 0) // 1000
            playing = "▶" if playback.get("is_playing") else "⏸"
            print(f"  {playing} {item['name']} — {artists}")
            print(f"     {progress//60}:{progress%60:02d} / {duration//60}:{duration%60:02d}")
            print(f"     Album: {item.get('album', {}).get('name', '?')}")
            if playback.get("context"):
                print(f"     Context: {playback['context'].get('uri', '?')}")
        else:
            print("  (no active playback)")

        print("\n=== State File ===")
        cmd_state()

    except Exception as e:
        print(f"Spotify API error: {e}")


def cmd_state():
    """Read /tmp/spotify_state.json."""
    state_file = "/tmp/spotify_state.json"
    if not os.path.exists(state_file):
        print(f"  {state_file} does not exist (librespot hasn't fired yet)")
        return
    try:
        with open(state_file) as f:
            state = json.load(f)
        import time as _time
        mtime = os.path.getmtime(state_file)
        age = _time.time() - mtime
        print(f"  Age: {age:.1f}s ago")
        print(f"  Connected: {state.get('connected')}")
        print(f"  Playing: {state.get('is_playing')}")
        track = state.get("track")
        if track:
            print(f"  Track: {track.get('name')} — {track.get('artists')}")
            print(f"  Album: {track.get('album')}")
            dur = track.get("duration_ms", 0) // 1000
            print(f"  Duration: {dur//60}:{dur%60:02d}")
        else:
            print("  Track: (none)")
    except Exception as e:
        print(f"  Error reading state file: {e}")


def cmd_simulate_playlist():
    """Simulate the playlist selection flow headlessly and show what would happen."""
    print("=== Simulating: PLAYLISTS → select → NOW_PLAYING ===\n")

    # Mock spotipy before importing project code
    import unittest.mock as mock
    spotipy_mock = MagicMock()

    # Simulate play_playlist succeeding
    spotipy_mock.Spotify.return_value.start_playback.return_value = None
    spotipy_mock.Spotify.return_value.devices.return_value = {
        "devices": [{"id": "fake-device-id", "name": "Kitchen Display", "is_active": False}]
    }
    sys.modules["spotipy"] = spotipy_mock
    sys.modules["spotipy.oauth2"] = spotipy_mock

    import display.renderer as _dr

    class FakeRenderer:
        def __init__(self):
            from config import DISPLAY_WIDTH, DISPLAY_HEIGHT
            from PIL import Image
            self.framebuffer = Image.new("1", (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
            self.regions = {}
            self.in_partial_mode = False
        def add_region(self, name, x, y, w, h):
            from display.regions import Region
            self.regions[name] = Region(name, x, y, w, h)
        def update_region(self, n, img): return True
        def render_region(self, n): pass
        def init(self): pass
        def init_partial(self): pass
        def clear(self): pass
        def sleep(self): pass

    _dr.DisplayRenderer = FakeRenderer

    from apps.music import MusicApp, MusicState
    from audio.spotify_api import SpotifyController

    sp = SpotifyController()
    app = MusicApp(FakeRenderer())
    app._running = False
    app.spotify = sp

    # Set up fake playlists
    app.music_state = MusicState.PLAYLISTS
    app._list_items = [
        {"name": "Britney Spears Songs (clean)", "track_count": 42,
         "image_url": "", "uri": "spotify:playlist:0pVTHWXSCBpFLNhYFADmQm", "id": "0pVT"},
    ]
    app._selected_index = 1  # Item 0 = Back, item 1 = playlist

    print(f"State before select(): {app.music_state}")
    print(f"Selected index: {app._selected_index}")
    print(f"track_name before: '{app.track_name}'")
    print(f"is_playing before: {app.is_playing}")
    print()

    result = app.select()

    print(f"\nAfter select():")
    print(f"  State: {app.music_state}")
    print(f"  track_name: '{app.track_name}'")
    print(f"  is_playing: {app.is_playing}")
    print(f"  _force_api_poll: {app._force_api_poll}")
    print(f"  select() returned: {result}")
    print()

    # Now simulate what the encoder bounce would do
    print("Simulating encoder bounce (navigate +1 then -1):")
    print(f"  navigate(+1) → ", end="")
    if app.music_state == MusicState.NOW_PLAYING:
        print("would call next_track() [BOUNCE BUG]")
    else:
        print("would scroll list [OK]")

    # Show the rendered screen
    app.render()
    out = "/tmp/debug_now_playing_starting.png"
    img = app.renderer.framebuffer.convert("RGB")
    img.save(out)
    print(f"\nRendered NOW_PLAYING screen → {out}")
    print("(Shows 'Starting...' because track_name is empty - expected)")


def cmd_screens():
    """Render all defined screens."""
    cmd_screenshot("all")


def cmd_logs(lines: int = 50):
    """Tail systemd journal for smart-display service."""
    for service in ["smart-display", "smart_display", "main"]:
        result = subprocess.run(
            ["journalctl", "-u", service, "-n", str(lines), "--no-pager"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"=== journalctl -u {service} (last {lines} lines) ===")
            print(result.stdout)
            return
    print("No systemd service found. Checking for running process...")
    cmd_running()


def cmd_running():
    """Check if main.py is running."""
    result = subprocess.run(
        ["pgrep", "-la", "python"],
        capture_output=True, text=True
    )
    lines = [l for l in result.stdout.splitlines() if "main.py" in l]
    if lines:
        print("Smart display IS running:")
        for l in lines:
            print(f"  {l}")
    else:
        print("Smart display is NOT running (no main.py process found)")


COMMANDS = {
    "screenshot": lambda args: cmd_screenshot(args[0] if args else "all"),
    "spotify": lambda args: cmd_spotify(),
    "state": lambda args: cmd_state(),
    "simulate": lambda args: cmd_simulate_playlist() if not args or args[0] == "playlist" else print(f"Unknown simulate target: {args[0]}"),
    "screens": lambda args: cmd_screens(),
    "logs": lambda args: cmd_logs(int(args[0]) if args else 50),
    "running": lambda args: cmd_running(),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(1)

    COMMANDS[cmd](args)


if __name__ == "__main__":
    main()

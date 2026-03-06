#!/usr/bin/env python3
"""
Render any app screen to PNG for visual debugging without physical hardware.

Usage:
    python scripts/screenshot.py [screen] [output.png]

Screens:
    music_menu          Music app - main menu (no track)
    music_menu_track    Music app - main menu (track playing)
    music_now_playing   Music app - NOW_PLAYING
    music_now_starting  Music app - NOW_PLAYING (no track yet, "Starting...")
    music_playlists     Music app - playlist list
    music_liked         Music app - liked songs list
    menu                Main menu
"""

import sys
import os

# Must be first - mock hardware modules before any project imports
from unittest.mock import MagicMock, patch

_epd_mock = MagicMock()
sys.modules["waveshare_epd"] = _epd_mock
sys.modules["waveshare_epd.epd7in5_V2"] = _epd_mock
sys.modules["gpiozero"] = MagicMock()
sys.modules["pvporcupine"] = MagicMock()
sys.modules["pvrhino"] = MagicMock()
sys.modules["sounddevice"] = MagicMock()
sys.modules["faster_whisper"] = MagicMock()
sys.modules["spotipy"] = MagicMock()
sys.modules["spotipy.oauth2"] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT


class FakeRenderer:
    """Hardware-free renderer that stores output in a PIL Image."""

    def __init__(self):
        self.framebuffer = Image.new("1", (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        self.regions: dict = {}
        self.in_partial_mode = False

    def add_region(self, name, x, y, width, height):
        from display.regions import Region
        self.regions[name] = Region(name, x, y, width, height)

    def update_region(self, name, img):
        return True

    def render_region(self, name):
        pass

    def init(self): pass
    def init_partial(self): pass
    def clear(self): pass
    def sleep(self): pass


# Patch DisplayRenderer before importing apps
import display.renderer as _dr
_dr.DisplayRenderer = FakeRenderer

# Now safe to import app modules
from apps.music import MusicApp, MusicState
from apps.menu import MenuApp, MenuItem


def make_music_app(state: MusicState, track: str = "", artist: str = "",
                   album: str = "", duration_ms: int = 0, position_ms: int = 0,
                   is_playing: bool = False, list_items=None,
                   list_title: str = "", selected: int = 0,
                   scroll: int = 0) -> MusicApp:
    renderer = FakeRenderer()
    app = MusicApp(renderer)
    app._running = False  # Stop background thread activity
    app.music_state = state
    app.track_name = track
    app.artist_name = artist
    app.album_name = album
    app.duration_ms = duration_ms
    app.position_ms = position_ms
    app.is_playing = is_playing
    app._selected_index = selected
    app._scroll_offset = scroll
    app._list_title = list_title
    app._loading = False
    app._load_error = False
    if list_items is not None:
        app._list_items = list_items
    return app


FAKE_TRACKS = [
    {"name": "...Baby One More Time", "artist": "Britney Spears", "duration_ms": 211000, "image_url": ""},
    {"name": "Toxic", "artist": "Britney Spears", "duration_ms": 198000, "image_url": ""},
    {"name": "Oops!... I Did It Again", "artist": "Britney Spears", "duration_ms": 204000, "image_url": ""},
    {"name": "Gimme More", "artist": "Britney Spears", "duration_ms": 237000, "image_url": ""},
    {"name": "Womanizer", "artist": "Britney Spears", "duration_ms": 224000, "image_url": ""},
    {"name": "If You Seek Amy", "artist": "Britney Spears", "duration_ms": 196000, "image_url": ""},
]

FAKE_PLAYLISTS = [
    {"name": "Britney Spears Songs (clean)", "track_count": 42, "image_url": "", "uri": "spotify:playlist:abc1", "id": "abc1"},
    {"name": "Guitar", "track_count": 18, "image_url": "", "uri": "spotify:playlist:abc2", "id": "abc2"},
    {"name": "Chill Vibes", "track_count": 65, "image_url": "", "uri": "spotify:playlist:abc3", "id": "abc3"},
    {"name": "Workout Bangers", "track_count": 30, "image_url": "", "uri": "spotify:playlist:abc4", "id": "abc4"},
]


SCREENS = {
    "music_menu": lambda: make_music_app(MusicState.MENU),
    "music_menu_track": lambda: make_music_app(
        MusicState.MENU, track="Toxic", artist="Britney Spears", is_playing=True),
    "music_now_playing": lambda: make_music_app(
        MusicState.NOW_PLAYING, track="Toxic", artist="Britney Spears",
        album="In the Zone", duration_ms=198000, position_ms=67000, is_playing=True),
    "music_now_starting": lambda: make_music_app(
        MusicState.NOW_PLAYING, track="", is_playing=True),
    "music_playlists": lambda: make_music_app(
        MusicState.PLAYLISTS, list_title="My Playlists", list_items=FAKE_PLAYLISTS,
        track="Toxic", is_playing=True),
    "music_liked": lambda: make_music_app(
        MusicState.LIKED, list_title="Liked Songs", list_items=FAKE_TRACKS,
        track="Toxic", is_playing=True, selected=2),
}


def render_screen(name: str) -> Image.Image:
    if name not in SCREENS:
        raise ValueError(f"Unknown screen '{name}'. Available: {list(SCREENS)}")
    app = SCREENS[name]()
    app.render()
    # Scale up 2x for easier viewing (e-ink is 1-bit, pixels are tiny)
    img = app.renderer.framebuffer.convert("RGB")
    return img


def main():
    screen = sys.argv[1] if len(sys.argv) > 1 else "music_menu"
    out = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/{screen}.png"

    if screen == "all":
        for name in SCREENS:
            path = f"/tmp/{name}.png"
            img = render_screen(name)
            img.save(path)
            print(f"Saved: {path}")
    else:
        img = render_screen(screen)
        img.save(out)
        print(f"Saved: {out}")


if __name__ == "__main__":
    main()

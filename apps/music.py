"""Music app - Spotify browse and playback control."""

import hashlib
import json
import os
import time
import threading
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Dict, List
from PIL import Image, ImageDraw, ImageFont, ImageOps

from display.renderer import DisplayRenderer
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, ASSETS_DIR


# State file written by librespot onevent callback
STATE_FILE = Path("/tmp/spotify_state.json")

# Album art cache directory
COVER_CACHE_DIR = Path("/tmp/spotify_covers")

# Convert ASSETS_DIR to Path for font loading
ASSETS_PATH = Path(ASSETS_DIR) if isinstance(ASSETS_DIR, str) else ASSETS_DIR

# Progress bar region constants (used in NOW_PLAYING)
PROGRESS_REGION_Y = 270
PROGRESS_REGION_HEIGHT = 50

# Thumbnail size for list views
THUMB_SIZE = 50

# Row height for list items
ROW_HEIGHT = 65

# Max visible items per page in list views
MAX_VISIBLE_LIST = 4

# Max visible items in the music menu
MAX_VISIBLE_MENU = 5


class MusicState(Enum):
    NOW_PLAYING     = "now_playing"
    MENU            = "menu"
    PLAYLISTS       = "playlists"
    PLAYLIST_TRACKS = "playlist_tracks"
    RECENT          = "recent"
    LIKED           = "liked"
    QUEUE           = "queue"


class MusicApp:
    """Music app with browse + playback control via Spotify."""

    # Album art display size in NOW_PLAYING
    ART_SIZE = 160

    def __init__(self, renderer: DisplayRenderer):
        self.renderer = renderer

        # Spotify API controller (injected by main after init)
        self.spotify = None

        # Current playback state (populated from state file)
        self.track_name: str = ""
        self.artist_name: str = ""
        self.album_name: str = ""
        self.duration_ms: int = 0
        self.position_ms: int = 0
        self.is_playing: bool = False
        self.volume: int = 100
        self.connected: bool = False
        self.cover_url: str = ""

        # Album art cache
        self._current_cover: Optional[Image.Image] = None
        self._current_cover_url: str = ""

        # Ensure cache directory exists
        COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Last update timestamps
        self._last_state_update: float = 0
        self._last_file_mtime: float = 0

        # Background update thread
        self._running = True
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

        # Callbacks
        self.on_update: Optional[Callable] = None
        self.on_progress_update: Optional[Callable] = None

        # Register progress bar region for partial refresh
        self.renderer.add_region(
            "music_progress",
            40,
            PROGRESS_REGION_Y,
            DISPLAY_WIDTH - 80,
            PROGRESS_REGION_HEIGHT,
        )

        # ---- State machine ----
        self.music_state = MusicState.MENU
        self._selected_index = 0
        self._scroll_offset = 0
        self._list_items: List[dict] = []
        self._list_title = ""
        self._loading = False
        self._load_error = False
        self._current_playlist_id = ""
        self._current_playlist_uri = ""
        self._thumb_cache: Dict[str, Optional[Image.Image]] = {}
        self._back_state: Optional[MusicState] = None  # Where "Back" returns to

        # Load fonts
        self._load_fonts()

    # ==================== Font Loading ====================

    def _load_fonts(self):
        """Load fonts for rendering."""
        try:
            self.font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            self.font_artist = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
            self.font_album = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
            self.font_time = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            self.font_status = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            self.font_hint = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
            self.font_menu_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
            self.font_menu_item = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
            self.font_item_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            self.font_item_sub = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        except OSError:
            default = ImageFont.load_default()
            (self.font_title, self.font_artist, self.font_album, self.font_time,
             self.font_status, self.font_hint, self.font_menu_bold,
             self.font_menu_item, self.font_item_title, self.font_item_sub) = [default] * 10

    # ==================== Cover Art ====================

    def _get_cover_cache_path(self, url: str) -> Path:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return COVER_CACHE_DIR / f"{url_hash}.png"

    def _download_cover(self, url: str) -> Optional[Image.Image]:
        """Download album cover and cache as 1-bit dithered PNG."""
        if not url:
            return None
        cache_path = self._get_cover_cache_path(url)
        if cache_path.exists():
            try:
                return Image.open(cache_path)
            except Exception:
                pass
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "SmartDisplay/1.0 (Raspberry Pi)"})
            with urllib.request.urlopen(req, timeout=10) as response:
                img_data = response.read()
            from io import BytesIO
            img = Image.open(BytesIO(img_data))
            img = img.resize((self.ART_SIZE, self.ART_SIZE), Image.Resampling.LANCZOS)
            img = img.convert("L").convert("1", dither=Image.Dither.FLOYDSTEINBERG)
            img.save(cache_path)
            return img
        except Exception as e:
            print(f"  [Failed to download cover: {e}]")
            return None

    def _get_cover_art(self) -> Optional[Image.Image]:
        """Return current cover art (cached)."""
        if not self.cover_url:
            return None
        if self.cover_url == self._current_cover_url and self._current_cover:
            return self._current_cover
        cover = self._download_cover(self.cover_url)
        if cover:
            self._current_cover = cover
            self._current_cover_url = self.cover_url
        return self._current_cover

    # ==================== Thumbnail Cache ====================

    def _fetch_thumb(self, url: str):
        """Download and resize a thumbnail (run in background thread)."""
        if url in self._thumb_cache and self._thumb_cache[url] is not None:
            return
        self._thumb_cache[url] = None  # Mark in-progress
        cover = self._download_cover(url)
        if cover:
            self._thumb_cache[url] = cover.resize(
                (THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)

    def _get_thumb(self, url: str) -> Optional[Image.Image]:
        """Return cached thumbnail, starting background fetch if not yet loaded."""
        if not url:
            return None
        if url not in self._thumb_cache:
            threading.Thread(target=self._fetch_thumb, args=(url,), daemon=True).start()
            return None
        return self._thumb_cache[url]

    def get_current_thumbnail(self, size: int = 32) -> Optional[Image.Image]:
        """Return current album art resized to size×size for the mini player."""
        cover = self._get_cover_art()
        if cover:
            return cover.resize((size, size), Image.Resampling.LANCZOS)
        return None

    # ==================== Async List Loading ====================

    def _load_async(self, fn, *args):
        """Reset list state and start background load."""
        self._list_items = []
        self._loading = True
        self._load_error = False
        self._selected_index = 0
        self._scroll_offset = 0
        threading.Thread(target=self._load_items, args=(fn, *args), daemon=True).start()

    def _load_items(self, fn, *args):
        """Load list items in background, then trigger re-render."""
        try:
            items = fn(*args)
            self._list_items = items
            # Pre-fetch thumbnails for the first 10 items
            for item in items[:10]:
                url = item.get("image_url", "")
                if url:
                    threading.Thread(
                        target=self._fetch_thumb, args=(url,), daemon=True).start()
        except Exception as e:
            print(f"  [Music: load error: {e}]")
            self._load_error = True
        finally:
            self._loading = False
            if self.on_update:
                self.on_update()

    # ==================== State Helpers ====================

    def _get_menu_items(self) -> List[str]:
        """Build the dynamic music menu item list."""
        items = []
        if self.track_name:
            items.append("▶ Now Playing")
        if self.spotify and self.spotify.available:
            items.append("⏸ Pause" if self.is_playing else "▶ Resume")
        items.append("♫ My Playlists")
        items.append("⏱ Recently Played")
        items.append("♥ Liked Songs")
        items.append("≡ Current Queue")
        items.append("← Exit Music")
        return items

    def on_new_track(self):
        """Auto-switch to NOW_PLAYING when a new track starts (called by main.py)."""
        if self.music_state in (
            MusicState.MENU, MusicState.PLAYLISTS,
            MusicState.RECENT, MusicState.LIKED,
            MusicState.QUEUE, MusicState.PLAYLIST_TRACKS,
        ):
            self.music_state = MusicState.NOW_PLAYING

    def reset_to_entry_state(self):
        """Called by main.py when entering the music app."""
        if self.track_name:
            self.music_state = MusicState.NOW_PLAYING
        else:
            self.music_state = MusicState.MENU
            self._selected_index = 0
            self._scroll_offset = 0

    # ==================== Background Update Loop ====================

    def _update_loop(self):
        """Background thread: read state file + advance position."""
        progress_update_counter = 0
        while self._running:
            self._read_state_file()
            if self.is_playing and self.duration_ms > 0:
                self.position_ms += 1000
                if self.position_ms > self.duration_ms:
                    self.position_ms = self.duration_ms
                progress_update_counter += 1
                if progress_update_counter >= 1 and self.on_progress_update:
                    self.on_progress_update()
                    progress_update_counter = 0
            time.sleep(1)

    def _read_state_file(self):
        """Read the state file written by the onevent callback."""
        try:
            if not STATE_FILE.exists():
                return
            mtime = STATE_FILE.stat().st_mtime
            if mtime <= self._last_file_mtime:
                return
            self._last_file_mtime = mtime
            with open(STATE_FILE) as f:
                state = json.load(f)

            self.connected = state.get("connected", False) or state.get("track") is not None
            track = state.get("track")
            new_track = False
            playback_changed = False

            if track:
                new_track = track.get("name", "") != self.track_name
                self.track_name = track.get("name", "")
                self.artist_name = track.get("artists", "")
                self.album_name = track.get("album", "")
                self.duration_ms = track.get("duration_ms", 0)
                self.cover_url = track.get("cover_url", "")
                if new_track:
                    self.position_ms = 0
                    threading.Thread(
                        target=self._download_cover,
                        args=(self.cover_url,),
                        daemon=True,
                    ).start()

            if "is_playing" in state:
                old_playing = self.is_playing
                self.is_playing = state["is_playing"]
                if old_playing != self.is_playing:
                    playback_changed = True

            if "position_ms" in state:
                self.position_ms = state["position_ms"]

            if "volume" in state:
                self.volume = state["volume"]

            self._last_state_update = time.time()

            if (new_track or playback_changed) and self.on_update:
                self.on_update()

        except (json.JSONDecodeError, IOError, KeyError):
            pass

    # ==================== Input Handlers ====================

    def navigate(self, direction: int):
        """Handle encoder rotation."""
        if self.music_state == MusicState.NOW_PLAYING:
            if self.spotify and self.spotify.available:
                if direction > 0:
                    # next_track works if there are queued tracks
                    self.spotify.next_track()
                elif self._current_playlist_uri:
                    # previous_track only works within a playlist/album context
                    self.spotify.previous_track()

        elif self.music_state == MusicState.MENU:
            items = self._get_menu_items()
            count = len(items)
            self._selected_index = max(0, min(count - 1, self._selected_index + direction))
            if self._selected_index < self._scroll_offset:
                self._scroll_offset = self._selected_index
            elif self._selected_index >= self._scroll_offset + MAX_VISIBLE_MENU:
                self._scroll_offset = self._selected_index - MAX_VISIBLE_MENU + 1

        else:
            # List views: index 0 = "← Back", 1+ = list items
            count = len(self._list_items) + 1
            self._selected_index = max(0, min(count - 1, self._selected_index + direction))
            if self._selected_index < self._scroll_offset:
                self._scroll_offset = self._selected_index
            elif self._selected_index >= self._scroll_offset + MAX_VISIBLE_LIST:
                self._scroll_offset = self._selected_index - MAX_VISIBLE_LIST + 1

    def select(self) -> bool:
        """Handle encoder press. Returns True to stay in app, False to exit to main menu."""
        if self.music_state == MusicState.NOW_PLAYING:
            # Press in NOW_PLAYING → go to MENU
            self.music_state = MusicState.MENU
            self._selected_index = 0
            self._scroll_offset = 0
            return True

        elif self.music_state == MusicState.MENU:
            items = self._get_menu_items()
            if self._selected_index >= len(items):
                return True
            selected = items[self._selected_index]

            if selected == "▶ Now Playing":
                self.music_state = MusicState.NOW_PLAYING
            elif selected in ("⏸ Pause", "▶ Resume"):
                if self.spotify and self.spotify.available:
                    self.spotify.toggle_play_pause(self.is_playing)
            elif selected == "♫ My Playlists":
                self.music_state = MusicState.PLAYLISTS
                self._list_title = "My Playlists"
                self._back_state = MusicState.MENU
                self._load_async(self.spotify.get_playlists)
            elif selected == "⏱ Recently Played":
                self.music_state = MusicState.RECENT
                self._list_title = "Recently Played"
                self._back_state = MusicState.MENU
                self._load_async(self.spotify.get_recently_played)
            elif selected == "♥ Liked Songs":
                self.music_state = MusicState.LIKED
                self._list_title = "Liked Songs"
                self._back_state = MusicState.MENU
                self._load_async(self.spotify.get_liked_songs)
            elif selected == "≡ Current Queue":
                self.music_state = MusicState.QUEUE
                self._list_title = "Current Queue"
                self._back_state = MusicState.MENU
                self._load_async(self.spotify.get_queue)
            elif selected == "← Exit Music":
                return False  # Signal main.py to go to main menu
            return True

        elif self.music_state == MusicState.PLAYLISTS:
            if self._selected_index == 0:
                # Back → MENU
                self.music_state = MusicState.MENU
                self._selected_index = 0
                self._scroll_offset = 0
                return True
            item_idx = self._selected_index - 1
            if item_idx < len(self._list_items):
                playlist = self._list_items[item_idx]
                self._current_playlist_id = playlist.get("id", "")
                self._current_playlist_uri = playlist.get("uri", "")
                self.music_state = MusicState.PLAYLIST_TRACKS
                self._list_title = playlist.get("name", "Playlist")
                self._back_state = MusicState.PLAYLISTS
                self._load_async(
                    self.spotify.get_playlist_tracks, self._current_playlist_id)
            return True

        else:
            # PLAYLIST_TRACKS, RECENT, LIKED, QUEUE
            if self._selected_index == 0:
                # Back
                back_to = self._back_state or MusicState.MENU
                self.music_state = back_to
                self._selected_index = 0
                self._scroll_offset = 0
                # If going back to PLAYLISTS, reload the list
                if back_to == MusicState.PLAYLISTS and self.spotify:
                    self._list_title = "My Playlists"
                    self._back_state = MusicState.MENU
                    self._load_async(self.spotify.get_playlists)
                return True
            item_idx = self._selected_index - 1
            if item_idx < len(self._list_items):
                track = self._list_items[item_idx]
                uri = track.get("uri", "")
                if uri and self.spotify and self.spotify.available:
                    context = (
                        self._current_playlist_uri
                        if self.music_state == MusicState.PLAYLIST_TRACKS
                        else None
                    )
                    played = self.spotify.play_track(uri, context)
                    if played:
                        # Pre-populate from list item immediately so NOW_PLAYING
                        # isn't blank while librespot fires the track_changed event
                        self.track_name = track.get("name", "")
                        self.artist_name = track.get("artist", "")
                        self.album_name = ""
                        self.duration_ms = track.get("duration_ms", 0)
                        self.position_ms = 0
                        self.is_playing = True
                        image_url = track.get("image_url", "")
                        if image_url:
                            self.cover_url = image_url
                            threading.Thread(
                                target=self._download_cover,
                                args=(image_url,), daemon=True).start()
                        self.music_state = MusicState.NOW_PLAYING
                    # If play failed, stay on list view (don't switch to blank NOW_PLAYING)
            return True

    def back(self) -> bool:
        """Handle encoder hold. Returns False (hold always goes to home)."""
        return False

    # ==================== Rendering ====================

    def render(self):
        """Render to framebuffer, dispatching by music_state."""
        img = Image.new("1", (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        if self.music_state == MusicState.NOW_PLAYING:
            self._render_now_playing(draw, img)
        elif self.music_state == MusicState.MENU:
            self._render_menu(draw)
        else:
            self._render_list(draw, img)

        self.renderer.framebuffer = img

    def _render_menu(self, draw: ImageDraw.Draw):
        """Render the music sub-menu."""
        draw.text((20, 12), "♫ Music", font=self.font_menu_bold, fill=0)
        draw.line([(0, 55), (DISPLAY_WIDTH, 55)], fill=0, width=2)

        items = self._get_menu_items()
        ITEM_HEIGHT = 62
        start_y = 65

        visible = items[self._scroll_offset: self._scroll_offset + MAX_VISIBLE_MENU]
        for i, item_text in enumerate(visible):
            actual_idx = self._scroll_offset + i
            item_y = start_y + i * ITEM_HEIGHT
            is_selected = (actual_idx == self._selected_index)

            if is_selected:
                draw.rectangle([0, item_y - 2, DISPLAY_WIDTH, item_y + ITEM_HEIGHT - 4], fill=0)
                draw.text((20, item_y + 10), item_text, font=self.font_menu_item, fill=1)
            else:
                draw.text((20, item_y + 10), item_text, font=self.font_menu_item, fill=0)
                # Divider below non-selected items (skip last visible)
                if i < len(visible) - 1:
                    draw.line(
                        [(0, item_y + ITEM_HEIGHT - 2), (DISPLAY_WIDTH, item_y + ITEM_HEIGHT - 2)],
                        fill=0, width=1)

        # Scroll arrows
        if self._scroll_offset > 0:
            draw.text((DISPLAY_WIDTH - 25, 60), "▲", font=self.font_hint, fill=0)
        if self._scroll_offset + MAX_VISIBLE_MENU < len(items):
            draw.text((DISPLAY_WIDTH - 25, 378), "▼", font=self.font_hint, fill=0)

        # Footer
        draw.line([(0, 390), (DISPLAY_WIDTH, 390)], fill=0, width=1)
        draw.text((15, 402), "Hold: Home", font=self.font_hint, fill=0)
        hint = "↕ Navigate  ↵ Select"
        bbox = draw.textbbox((0, 0), hint, font=self.font_hint)
        draw.text((DISPLAY_WIDTH - (bbox[2] - bbox[0]) - 15, 402), hint, font=self.font_hint, fill=0)

    def _render_list(self, draw: ImageDraw.Draw, img: Image.Image):
        """Render a scrollable list view (playlists, tracks, etc.)."""
        # Header
        title_trunc = self._truncate_text(self._list_title, self.font_menu_bold, 500)
        draw.text((20, 12), title_trunc, font=self.font_menu_bold, fill=0)
        back_hint = "↵ Back ▶"
        bbox = draw.textbbox((0, 0), back_hint, font=self.font_hint)
        draw.text(
            (DISPLAY_WIDTH - (bbox[2] - bbox[0]) - 15, 20),
            back_hint, font=self.font_hint, fill=0)
        draw.line([(0, 55), (DISPLAY_WIDTH, 55)], fill=0, width=2)

        if self._loading:
            text = "Loading..."
            bbox = draw.textbbox((0, 0), text, font=self.font_status)
            draw.text(
                ((DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2, 190),
                text, font=self.font_status, fill=0)
        elif self._load_error:
            text = "Couldn't load data"
            bbox = draw.textbbox((0, 0), text, font=self.font_status)
            draw.text(
                ((DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2, 190),
                text, font=self.font_status, fill=0)
        else:
            total_items = len(self._list_items) + 1  # +1 for "← Back"
            start_y = 65

            for i in range(MAX_VISIBLE_LIST):
                actual_idx = self._scroll_offset + i
                if actual_idx >= total_items:
                    break

                item_y = start_y + i * ROW_HEIGHT
                is_selected = (actual_idx == self._selected_index)

                if is_selected:
                    draw.rectangle([0, item_y, DISPLAY_WIDTH, item_y + ROW_HEIGHT - 1], fill=0)
                    text_fill = 1
                else:
                    text_fill = 0
                    # Divider below non-selected, non-last items
                    if i < MAX_VISIBLE_LIST - 1 and actual_idx < total_items - 1:
                        draw.line(
                            [(0, item_y + ROW_HEIGHT - 1),
                             (DISPLAY_WIDTH, item_y + ROW_HEIGHT - 1)],
                            fill=0, width=1)

                if actual_idx == 0:
                    # "← Back" item (centered vertically, no thumb)
                    label = "← Back"
                    bbox = draw.textbbox((0, 0), label, font=self.font_item_title)
                    text_y = item_y + (ROW_HEIGHT - (bbox[3] - bbox[1])) // 2
                    draw.text((20, text_y), label, font=self.font_item_title, fill=text_fill)
                else:
                    data_idx = actual_idx - 1
                    item = self._list_items[data_idx]
                    name = item.get("name", "")
                    subtitle = item.get("artist", "")
                    if not subtitle:
                        tc = item.get("track_count", "")
                        subtitle = f"{tc} tracks" if tc else ""
                    image_url = item.get("image_url", "")

                    # Thumbnail
                    thumb = self._get_thumb(image_url)
                    thumb_x = 8
                    thumb_top = item_y + (ROW_HEIGHT - THUMB_SIZE) // 2

                    if thumb:
                        if is_selected:
                            # Invert for dark background
                            thumb_inv = ImageOps.invert(thumb.convert("L")).convert("1")
                            img.paste(thumb_inv, (thumb_x, thumb_top))
                        else:
                            img.paste(thumb, (thumb_x, thumb_top))
                    else:
                        draw.rectangle(
                            [thumb_x, thumb_top, thumb_x + THUMB_SIZE, thumb_top + THUMB_SIZE],
                            outline=text_fill, width=1)
                        draw.text(
                            (thumb_x + 14, thumb_top + 14), "♪",
                            font=self.font_hint, fill=text_fill)

                    # Text
                    text_x = thumb_x + THUMB_SIZE + 10
                    max_w = DISPLAY_WIDTH - text_x - 15
                    draw.text(
                        (text_x, item_y + 8),
                        self._truncate_text(name, self.font_item_title, max_w),
                        font=self.font_item_title, fill=text_fill)
                    if subtitle:
                        draw.text(
                            (text_x, item_y + 36),
                            self._truncate_text(str(subtitle), self.font_item_sub, max_w),
                            font=self.font_item_sub, fill=text_fill)

            # Scroll arrows
            if self._scroll_offset > 0:
                draw.text((DISPLAY_WIDTH - 25, 60), "▲", font=self.font_hint, fill=0)
            total_items = len(self._list_items) + 1
            if self._scroll_offset + MAX_VISIBLE_LIST < total_items:
                draw.text((DISPLAY_WIDTH - 25, 330), "▼", font=self.font_hint, fill=0)

        # Footer
        draw.line([(0, 355), (DISPLAY_WIDTH, 355)], fill=0, width=1)
        draw.text((15, 367), "Hold: Home", font=self.font_hint, fill=0)
        if self.music_state == MusicState.PLAYLISTS:
            action = "↕ Navigate  ↵ Open"
        else:
            action = "↕ Navigate  ↵ Play"
        bbox = draw.textbbox((0, 0), action, font=self.font_hint)
        draw.text(
            (DISPLAY_WIDTH - (bbox[2] - bbox[0]) - 15, 367),
            action, font=self.font_hint, fill=0)

    def _render_now_playing(self, draw: ImageDraw.Draw, img: Image.Image):
        """Render the now-playing view."""
        # Header
        draw.text((20, 15), "Music", font=self.font_title, fill=0)
        status = "Playing" if self.is_playing else "Paused"
        status_bbox = draw.textbbox((0, 0), status, font=self.font_status)
        draw.text(
            (DISPLAY_WIDTH - (status_bbox[2] - status_bbox[0]) - 20, 20),
            status, font=self.font_status, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Album art (left side)
        art_x, art_y = 40, 80
        cover = self._get_cover_art()
        if cover:
            img.paste(cover, (art_x, art_y))
            draw.rectangle(
                [art_x - 2, art_y - 2, art_x + self.ART_SIZE + 1, art_y + self.ART_SIZE + 1],
                outline=0, width=2)
        else:
            draw.rounded_rectangle(
                [art_x, art_y, art_x + self.ART_SIZE, art_y + self.ART_SIZE],
                radius=10, outline=0, width=2)
            try:
                note_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 60)
            except OSError:
                note_font = self.font_title
            note_bbox = draw.textbbox((0, 0), "♪", font=note_font)
            draw.text(
                (art_x + (self.ART_SIZE - (note_bbox[2] - note_bbox[0])) // 2,
                 art_y + (self.ART_SIZE - (note_bbox[3] - note_bbox[1])) // 2),
                "♪", font=note_font, fill=0)

        # Track info (right side)
        info_x = art_x + self.ART_SIZE + 30
        info_max_w = DISPLAY_WIDTH - info_x - 50
        info_y = 90

        draw.text(
            (info_x, info_y),
            self._truncate_text(self.track_name.replace("\n", " "), self.font_title, info_max_w),
            font=self.font_title, fill=0)
        info_y += 50

        draw.text(
            (info_x, info_y),
            self._truncate_text(
                self.artist_name.replace("\n", ", "), self.font_artist, info_max_w),
            font=self.font_artist, fill=0)
        info_y += 38

        draw.text(
            (info_x, info_y),
            self._truncate_text(self.album_name.replace("\n", " "), self.font_album, info_max_w),
            font=self.font_album, fill=0)
        info_y += 35

        if self.duration_ms > 0:
            draw.text((info_x, info_y), self._format_time(self.duration_ms),
                      font=self.font_time, fill=0)

        # Progress bar
        bar_margin = 40
        bar_height = 10
        bar_y = 280
        bar_width = DISPLAY_WIDTH - 2 * bar_margin
        draw.rectangle(
            [bar_margin, bar_y, bar_margin + bar_width, bar_y + bar_height],
            outline=0, width=2)
        if self.duration_ms > 0:
            progress = min(1.0, self.position_ms / self.duration_ms)
            fill_width = int(bar_width * progress)
            if fill_width > 4:
                draw.rectangle(
                    [bar_margin + 2, bar_y + 2,
                     bar_margin + fill_width - 2, bar_y + bar_height - 2],
                    fill=0)

        # Time display
        time_y = bar_y + bar_height + 8
        current_time = self._format_time(self.position_ms)
        total_time = self._format_time(self.duration_ms)
        draw.text((bar_margin, time_y), current_time, font=self.font_time, fill=0)
        total_bbox = draw.textbbox((0, 0), total_time, font=self.font_time)
        draw.text(
            (bar_margin + bar_width - (total_bbox[2] - total_bbox[0]), time_y),
            total_time, font=self.font_time, fill=0)

        # Control hint
        if self.spotify and self.spotify.available:
            hint = "Rotate: Skip  |  Press: Menu  |  Hold: Home"
        else:
            hint = "Control playback from Spotify app"
        hint_bbox = draw.textbbox((0, 0), hint, font=self.font_hint)
        draw.text(
            ((DISPLAY_WIDTH - (hint_bbox[2] - hint_bbox[0])) // 2, 340),
            hint, font=self.font_hint, fill=0)

        # Footer
        draw.line(
            [(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)],
            fill=0, width=1)
        home_hint = "Hold: Home"
        bbox = draw.textbbox((0, 0), home_hint, font=self.font_hint)
        draw.text(
            (DISPLAY_WIDTH - (bbox[2] - bbox[0]) - 20, DISPLAY_HEIGHT - 35),
            home_hint, font=self.font_hint, fill=0)

    def render_progress_region(self) -> Image.Image:
        """Render just the progress bar region for partial refresh."""
        region = self.renderer.regions["music_progress"]
        img = Image.new("1", (region.width, region.height), 1)
        draw = ImageDraw.Draw(img)

        bar_height = 10
        bar_y = 10
        bar_width = region.width

        draw.rectangle([0, bar_y, bar_width, bar_y + bar_height], outline=0, width=2)
        if self.duration_ms > 0:
            progress = min(1.0, self.position_ms / self.duration_ms)
            fill_width = int(bar_width * progress)
            if fill_width > 4:
                draw.rectangle(
                    [2, bar_y + 2, fill_width - 2, bar_y + bar_height - 2], fill=0)

        time_y = bar_y + bar_height + 8
        current_time = self._format_time(self.position_ms)
        total_time = self._format_time(self.duration_ms)
        draw.text((0, time_y), current_time, font=self.font_time, fill=0)
        total_bbox = draw.textbbox((0, 0), total_time, font=self.font_time)
        draw.text(
            (bar_width - (total_bbox[2] - total_bbox[0]), time_y),
            total_time, font=self.font_time, fill=0)
        return img

    def update_progress(self):
        """Update just the progress bar region with a partial refresh."""
        if not self.track_name or self.music_state != MusicState.NOW_PLAYING:
            return
        progress_img = self.render_progress_region()
        if self.renderer.update_region("music_progress", progress_img):
            self.renderer.render_region("music_progress")

    # ==================== Utilities ====================

    def _truncate_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
        """Truncate text with ellipsis to fit within max_width pixels."""
        if not text:
            return ""
        draw = ImageDraw.Draw(Image.new("1", (1, 1), 1))
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return text
        ellipsis = "..."
        low, high = 0, len(text) - 1
        while low < high:
            mid = (low + high + 1) // 2
            truncated = text[:mid] + ellipsis
            bbox = draw.textbbox((0, 0), truncated, font=font)
            if bbox[2] - bbox[0] <= max_width:
                low = mid
            else:
                high = mid - 1
        return text[:low] + ellipsis if low > 0 else ellipsis

    def _format_time(self, ms: int) -> str:
        """Format milliseconds as M:SS."""
        total_seconds = ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:02d}"

    def shutdown(self):
        """Stop the update thread."""
        self._running = False

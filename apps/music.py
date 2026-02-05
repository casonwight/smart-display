"""Music app - display Spotify playback info from Spotify Connect."""

import hashlib
import json
import os
import time
import threading
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
from PIL import Image, ImageDraw, ImageFont

from display.renderer import DisplayRenderer
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, ASSETS_DIR


# State file written by librespot onevent callback
STATE_FILE = Path("/tmp/spotify_state.json")

# Album art cache directory
COVER_CACHE_DIR = Path("/tmp/spotify_covers")

# Convert ASSETS_DIR to Path for font loading
ASSETS_PATH = Path(ASSETS_DIR) if isinstance(ASSETS_DIR, str) else ASSETS_DIR

# Progress bar region constants
PROGRESS_REGION_Y = 270
PROGRESS_REGION_HEIGHT = 50


class MusicApp:
    """Display-only music app that shows current Spotify playback."""

    # Album art display size
    ART_SIZE = 160

    def __init__(self, renderer: DisplayRenderer):
        self.renderer = renderer

        # Current playback state
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

        # Last update timestamp
        self._last_state_update: float = 0
        self._last_file_mtime: float = 0

        # Background thread for position updates
        self._running = True
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

        # Callback for when display needs update (full redraw)
        self.on_update: Optional[Callable] = None

        # Callback for progress-only update (partial refresh)
        self.on_progress_update: Optional[Callable] = None

        # Register progress bar region for partial refresh
        self.renderer.add_region(
            "music_progress",
            40,  # x - matches bar_margin
            PROGRESS_REGION_Y,
            DISPLAY_WIDTH - 80,  # width
            PROGRESS_REGION_HEIGHT
        )

        # Load fonts
        self._load_fonts()

    def _load_fonts(self):
        """Load fonts for rendering."""
        # Try system fonts first, then assets folder
        try:
            self.font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            self.font_artist = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
            self.font_album = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
            self.font_time = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            self.font_status = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            self.font_hint = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except OSError:
            # Fallback to default font
            self.font_title = ImageFont.load_default()
            self.font_artist = ImageFont.load_default()
            self.font_album = ImageFont.load_default()
            self.font_time = ImageFont.load_default()
            self.font_status = ImageFont.load_default()
            self.font_hint = ImageFont.load_default()

    def _get_cover_cache_path(self, url: str) -> Path:
        """Get cache file path for a cover URL."""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return COVER_CACHE_DIR / f"{url_hash}.png"

    def _download_cover(self, url: str) -> Optional[Image.Image]:
        """Download album cover and convert to 1-bit dithered image."""
        if not url:
            return None

        cache_path = self._get_cover_cache_path(url)

        # Check cache first
        if cache_path.exists():
            try:
                return Image.open(cache_path)
            except Exception:
                pass

        # Download the image
        try:
            # Create request with User-Agent to avoid 403 errors
            request = urllib.request.Request(
                url,
                headers={'User-Agent': 'SmartDisplay/1.0 (Raspberry Pi)'}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                img_data = response.read()

            # Open and process image
            from io import BytesIO
            img = Image.open(BytesIO(img_data))

            # Resize to art size
            img = img.resize((self.ART_SIZE, self.ART_SIZE), Image.Resampling.LANCZOS)

            # Convert to grayscale then dither to 1-bit
            img = img.convert('L')  # Grayscale
            img = img.convert('1', dither=Image.Dither.FLOYDSTEINBERG)  # Dithered 1-bit

            # Cache the processed image
            img.save(cache_path)

            return img

        except Exception as e:
            print(f"  [Failed to download cover: {e}]")
            return None

    def _get_cover_art(self) -> Optional[Image.Image]:
        """Get the current cover art, downloading if needed."""
        if not self.cover_url:
            return None

        # Return cached if same URL
        if self.cover_url == self._current_cover_url and self._current_cover:
            return self._current_cover

        # Download new cover in background to avoid blocking
        cover = self._download_cover(self.cover_url)
        if cover:
            self._current_cover = cover
            self._current_cover_url = self.cover_url

        return self._current_cover

    def _update_loop(self):
        """Background thread to read state file and update position."""
        progress_update_counter = 0
        while self._running:
            self._read_state_file()

            # If playing, increment position
            if self.is_playing and self.duration_ms > 0:
                self.position_ms += 1000
                if self.position_ms > self.duration_ms:
                    self.position_ms = self.duration_ms

                # Trigger progress update every 5 seconds (to avoid too many refreshes)
                progress_update_counter += 1
                if progress_update_counter >= 5 and self.on_progress_update:
                    self.on_progress_update()
                    progress_update_counter = 0

            time.sleep(1)

    def _read_state_file(self):
        """Read the state file written by the onevent callback."""
        try:
            if not STATE_FILE.exists():
                return

            # Check if file was modified
            mtime = STATE_FILE.stat().st_mtime
            if mtime <= self._last_file_mtime:
                return

            self._last_file_mtime = mtime

            with open(STATE_FILE) as f:
                state = json.load(f)

            # Update connected state
            self.connected = state.get("connected", False) or state.get("track") is not None

            # Update track info
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

                # Reset position on new track
                if new_track:
                    self.position_ms = 0
                    # Pre-fetch cover art for new track
                    threading.Thread(
                        target=self._download_cover,
                        args=(self.cover_url,),
                        daemon=True
                    ).start()

            # Update playback state
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

            # Only trigger display update on track change or playback state change
            # Not on every position update (reduces flickering)
            if (new_track or playback_changed) and self.on_update:
                self.on_update()

        except (json.JSONDecodeError, IOError, KeyError):
            pass

    def navigate(self, direction: int):
        """Handle encoder rotation - could adjust volume in future."""
        pass

    def select(self) -> bool:
        """Handle encoder press - returns False to exit to menu."""
        return False

    def back(self) -> bool:
        """Handle encoder hold - return to home. Returns False to exit."""
        return False

    def render(self):
        """Render the music app to the framebuffer."""
        # Create fresh 1-bit image
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        # Check if we have track info
        if not self.track_name:
            self._render_idle(draw)
        else:
            self._render_now_playing(draw, img)

        self.renderer.framebuffer = img

    def _render_idle(self, draw: ImageDraw.Draw):
        """Render idle state when no music is playing."""
        # Title
        draw.text((20, 15), "Music", font=self.font_title, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Music note icon (simple text representation)
        note = "♪"
        try:
            note_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 80)
        except OSError:
            note_font = self.font_title
        bbox = draw.textbbox((0, 0), note, font=note_font)
        note_width = bbox[2] - bbox[0]
        draw.text(
            ((DISPLAY_WIDTH - note_width) // 2, 120),
            note,
            font=note_font,
            fill=0
        )

        # Status message
        status = "No music playing"
        bbox = draw.textbbox((0, 0), status, font=self.font_status)
        status_width = bbox[2] - bbox[0]
        draw.text(
            ((DISPLAY_WIDTH - status_width) // 2, 240),
            status,
            font=self.font_status,
            fill=0
        )

        # Instructions
        hint1 = "Open Spotify on your phone"
        hint2 = "Select 'Kitchen Display' to play"
        bbox1 = draw.textbbox((0, 0), hint1, font=self.font_hint)
        bbox2 = draw.textbbox((0, 0), hint2, font=self.font_hint)
        draw.text(
            ((DISPLAY_WIDTH - (bbox1[2] - bbox1[0])) // 2, 300),
            hint1,
            font=self.font_hint,
            fill=0
        )
        draw.text(
            ((DISPLAY_WIDTH - (bbox2[2] - bbox2[0])) // 2, 330),
            hint2,
            font=self.font_hint,
            fill=0
        )

        # Footer with back button (wider box)
        draw.line([(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)], fill=0, width=1)
        draw.rounded_rectangle([15, DISPLAY_HEIGHT - 42, 105, DISPLAY_HEIGHT - 12], radius=8, outline=0, width=2)
        draw.text((25, DISPLAY_HEIGHT - 39), "← Back", font=self.font_hint, fill=0)
        # Shorter hint on right
        hint = "Hold: Home"
        bbox = draw.textbbox((0, 0), hint, font=self.font_hint)
        draw.text((DISPLAY_WIDTH - (bbox[2] - bbox[0]) - 20, DISPLAY_HEIGHT - 35), hint, font=self.font_hint, fill=0)

    def _render_now_playing(self, draw: ImageDraw.Draw, img: Image.Image):
        """Render now playing view."""
        # Header with status
        draw.text((20, 15), "Music", font=self.font_title, fill=0)
        status = "Playing" if self.is_playing else "Paused"
        status_bbox = draw.textbbox((0, 0), status, font=self.font_status)
        status_x = DISPLAY_WIDTH - (status_bbox[2] - status_bbox[0]) - 20
        draw.text((status_x, 20), status, font=self.font_status, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Content area - left side: album art
        art_x = 40
        art_y = 80

        # Try to get album cover
        cover = self._get_cover_art()
        if cover:
            # Paste the dithered album art
            img.paste(cover, (art_x, art_y))
            # Draw border around it
            draw.rectangle(
                [art_x - 2, art_y - 2, art_x + self.ART_SIZE + 1, art_y + self.ART_SIZE + 1],
                outline=0, width=2
            )
        else:
            # Fallback: music note placeholder
            draw.rounded_rectangle(
                [art_x, art_y, art_x + self.ART_SIZE, art_y + self.ART_SIZE],
                radius=10, outline=0, width=2
            )
            note = "♪"
            try:
                note_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 60)
            except OSError:
                note_font = self.font_title
            note_bbox = draw.textbbox((0, 0), note, font=note_font)
            note_x = art_x + (self.ART_SIZE - (note_bbox[2] - note_bbox[0])) // 2
            note_y = art_y + (self.ART_SIZE - (note_bbox[3] - note_bbox[1])) // 2
            draw.text((note_x, note_y), note, font=note_font, fill=0)

        # Right side: track info (with more margin on right)
        info_x = art_x + self.ART_SIZE + 30
        info_max_width = DISPLAY_WIDTH - info_x - 50  # More right margin
        info_y = 90

        # Track name - clean any newlines just in case
        track_clean = self.track_name.replace('\n', ' ')
        track_display = self._truncate_text(track_clean, self.font_title, info_max_width)
        draw.text((info_x, info_y), track_display, font=self.font_title, fill=0)
        info_y += 50

        # Artist name - replace newlines with commas (Spotify sends multiple artists with \n)
        artist_clean = self.artist_name.replace('\n', ', ')
        artist_display = self._truncate_text(artist_clean, self.font_artist, info_max_width)
        draw.text((info_x, info_y), artist_display, font=self.font_artist, fill=0)
        info_y += 38

        # Album name - clean any newlines just in case
        album_clean = self.album_name.replace('\n', ' ')
        album_display = self._truncate_text(album_clean, self.font_album, info_max_width)
        draw.text((info_x, info_y), album_display, font=self.font_album, fill=0)
        info_y += 35

        # Duration
        if self.duration_ms > 0:
            duration_text = self._format_time(self.duration_ms)
            draw.text((info_x, info_y), duration_text, font=self.font_time, fill=0)

        # Progress bar
        bar_margin = 40
        bar_height = 10
        bar_y = 280
        bar_width = DISPLAY_WIDTH - 2 * bar_margin

        # Background bar (outline only)
        draw.rectangle(
            [bar_margin, bar_y, bar_margin + bar_width, bar_y + bar_height],
            outline=0, width=2
        )

        # Progress fill
        if self.duration_ms > 0:
            progress = min(1.0, self.position_ms / self.duration_ms)
            fill_width = int(bar_width * progress)
            if fill_width > 4:
                draw.rectangle(
                    [bar_margin + 2, bar_y + 2, bar_margin + fill_width - 2, bar_y + bar_height - 2],
                    fill=0
                )

        # Time display below bar
        time_y = bar_y + bar_height + 8
        current_time = self._format_time(self.position_ms)
        total_time = self._format_time(self.duration_ms)
        draw.text((bar_margin, time_y), current_time, font=self.font_time, fill=0)
        total_bbox = draw.textbbox((0, 0), total_time, font=self.font_time)
        draw.text(
            (bar_margin + bar_width - (total_bbox[2] - total_bbox[0]), time_y),
            total_time,
            font=self.font_time,
            fill=0
        )

        # Control hint
        hint = "Control playback from Spotify app"
        hint_bbox = draw.textbbox((0, 0), hint, font=self.font_hint)
        hint_x = (DISPLAY_WIDTH - (hint_bbox[2] - hint_bbox[0])) // 2
        draw.text((hint_x, 340), hint, font=self.font_hint, fill=0)

        # Footer with back button (wider box)
        draw.line([(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)], fill=0, width=1)
        draw.rounded_rectangle([15, DISPLAY_HEIGHT - 42, 105, DISPLAY_HEIGHT - 12], radius=8, outline=0, width=2)
        draw.text((25, DISPLAY_HEIGHT - 39), "← Back", font=self.font_hint, fill=0)
        # Shorter hint on right
        back_hint = "Hold: Home"
        bbox = draw.textbbox((0, 0), back_hint, font=self.font_hint)
        draw.text((DISPLAY_WIDTH - (bbox[2] - bbox[0]) - 20, DISPLAY_HEIGHT - 35), back_hint, font=self.font_hint, fill=0)

    def render_progress_region(self) -> Image.Image:
        """Render just the progress bar region for partial refresh."""
        region = self.renderer.regions["music_progress"]
        img = Image.new('1', (region.width, region.height), 1)
        draw = ImageDraw.Draw(img)

        bar_height = 10
        bar_y = 10  # Relative to region top
        bar_width = region.width

        # Background bar (outline only)
        draw.rectangle(
            [0, bar_y, bar_width, bar_y + bar_height],
            outline=0, width=2
        )

        # Progress fill
        if self.duration_ms > 0:
            progress = min(1.0, self.position_ms / self.duration_ms)
            fill_width = int(bar_width * progress)
            if fill_width > 4:
                draw.rectangle(
                    [2, bar_y + 2, fill_width - 2, bar_y + bar_height - 2],
                    fill=0
                )

        # Time display below bar
        time_y = bar_y + bar_height + 8
        current_time = self._format_time(self.position_ms)
        total_time = self._format_time(self.duration_ms)
        draw.text((0, time_y), current_time, font=self.font_time, fill=0)
        total_bbox = draw.textbbox((0, 0), total_time, font=self.font_time)
        draw.text(
            (bar_width - (total_bbox[2] - total_bbox[0]), time_y),
            total_time,
            font=self.font_time,
            fill=0
        )

        return img

    def update_progress(self):
        """Update just the progress bar region with partial refresh."""
        if not self.track_name:
            return  # No track playing

        progress_img = self.render_progress_region()
        if self.renderer.update_region("music_progress", progress_img):
            self.renderer.render_region("music_progress")

    def _truncate_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
        """Truncate text to fit within max_width, adding ellipsis if needed."""
        if not text:
            return ""

        # Check if text fits as-is
        draw = ImageDraw.Draw(Image.new('1', (1, 1), 1))
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return text  # Fits without truncation

        # Binary search for best truncation point
        ellipsis = "..."
        low, high = 0, len(text) - 1  # -1 since we need room for ellipsis
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
        """Format milliseconds as MM:SS."""
        total_seconds = ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:02d}"

    def shutdown(self):
        """Stop the update thread."""
        self._running = False

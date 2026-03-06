#!/usr/bin/env python3
"""
Smart Display Sandbox
=====================
Runs the FULL MainController with all hardware mocked.
Drive it with Python calls, screenshot after every action.
Exactly mimics what you see + do physically on the device.

Usage
-----
Interactive (python -i):
    python -i scripts/sandbox.py
    >>> sb = Sandbox()
    >>> sb.nav(2)            # scroll down 2 (like rotating encoder right)
    >>> sb.press()           # press encoder button
    >>> sb.ss("after_press") # screenshot → /tmp/sb_after_press.png
    >>> sb.state()           # print current state summary

Run a scenario script:
    python scripts/sandbox.py run /tmp/my_scenario.py

Built-in scenarios:
    python scripts/sandbox.py demo music_playlist
    python scripts/sandbox.py demo menu_navigation
    python scripts/sandbox.py demo bug_playlist_skip

All screenshots land in /tmp/sb_*.png — read them with the Read tool.
"""

import sys
import os
import time
import threading
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── 1. Root path ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ── 2. Fake hardware classes ──────────────────────────────────────────────────

class FakeRotaryEncoder:
    """Stores callbacks set by MainController; call rotate_* to trigger them."""
    def __init__(self, *a, **kw):
        self._cw = None
        self._ccw = None
        self.max_steps = kw.get("max_steps", 0)

    @property
    def when_rotated_clockwise(self): return self._cw
    @when_rotated_clockwise.setter
    def when_rotated_clockwise(self, fn): self._cw = fn

    @property
    def when_rotated_counter_clockwise(self): return self._ccw
    @when_rotated_counter_clockwise.setter
    def when_rotated_counter_clockwise(self, fn): self._ccw = fn

    def rotate_cw(self):
        if self._cw: self._cw()

    def rotate_ccw(self):
        if self._ccw: self._ccw()


class FakeButton:
    """Stores callbacks set by MainController; call trigger_* to fire them."""
    def __init__(self, *a, **kw):
        self._pressed = None
        self._released = None
        self._held = None
        self.is_pressed = False

    @property
    def when_pressed(self): return self._pressed
    @when_pressed.setter
    def when_pressed(self, fn): self._pressed = fn

    @property
    def when_released(self): return self._released
    @when_released.setter
    def when_released(self, fn): self._released = fn

    @property
    def when_held(self): return self._held
    @when_held.setter
    def when_held(self, fn): self._held = fn

    def trigger_press(self):
        self.is_pressed = True
        if self._pressed: self._pressed()

    def trigger_release(self):
        self.is_pressed = False
        if self._released: self._released()

    def trigger_hold(self):
        self.is_pressed = True
        if self._held: self._held()
        self.is_pressed = False


class FakeAudioPlayer:
    """No-op audio — tracks volume so overlay works."""
    def __init__(self, *a, **kw):
        self.volume = 50
        self._volume = 50

    def volume_up(self):
        self._volume = min(100, self._volume + 5)
        self.volume = self._volume
        return self._volume

    def volume_down(self):
        self._volume = max(0, self._volume - 5)
        self.volume = self._volume
        return self._volume

    def play(self, *a, **kw): pass
    def stop(self): pass
    def shutdown(self): pass
    def _set_system_volume(self, v): pass
    def _init_hardware(self): pass


class FakeTTS:
    """Prints instead of speaking."""
    volume = 50
    on_speaking_changed = None

    def speak_async(self, text):
        print(f"  [TTS] \"{text}\"")

    def stop(self): pass
    def shutdown(self): pass


class FakeVoice:
    """No-op voice controller."""
    def __init__(self, *a, **kw): pass
    def start(self): pass
    def stop(self): pass


class SandboxRenderer:
    """
    Drop-in replacement for DisplayRenderer.
    Keeps a real PIL framebuffer; all hardware calls are no-ops.
    Call .save(path) to capture what the e-ink would show.
    """
    def __init__(self):
        from PIL import Image
        from config import DISPLAY_WIDTH, DISPLAY_HEIGHT
        self.width = DISPLAY_WIDTH
        self.height = DISPLAY_HEIGHT
        self.framebuffer = Image.new('1', (self.width, self.height), 1)
        self.regions = {}
        self.in_partial_mode = False
        self.partial_refresh_count = 0
        self.max_partial = 9999
        # epd is a no-op mock; getbuffer just returns bytes so the caller doesn't crash
        self.epd = MagicMock()
        self.epd.getbuffer.side_effect = lambda img: b""

    # ── Methods called by MainController._render_internal ──
    def init(self):
        self.in_partial_mode = False

    def init_partial(self):
        self.in_partial_mode = True

    def clear(self):
        from PIL import Image
        self.framebuffer = Image.new('1', (self.width, self.height), 1)

    def sleep(self): pass

    # ── Region management (used by MusicApp) ──
    def add_region(self, name, x, y, width, height):
        from display.regions import Region
        self.regions[name] = Region(name, x, y, width, height)

    def update_region(self, name, img):
        if name in self.regions:
            r = self.regions[name]
            if img.size != (r.width, r.height):
                img = img.resize((r.width, r.height))
            self.framebuffer.paste(img, (r.x, r.y))
        return True

    def render_region(self, name): pass

    def get_draw(self):
        from PIL import ImageDraw
        return self.framebuffer, ImageDraw.Draw(self.framebuffer)

    def save(self, path: str):
        self.framebuffer.convert("RGB").save(path)


# ── 3. Patch all hardware before importing main ───────────────────────────────

# Hardware driver modules
_epd_mod = MagicMock()
sys.modules['waveshare_epd'] = _epd_mod
sys.modules['waveshare_epd.epd7in5_V2'] = _epd_mod

# gpiozero — inject our fake classes
_gpio_mod = MagicMock()
_gpio_mod.RotaryEncoder = FakeRotaryEncoder
_gpio_mod.Button = FakeButton
sys.modules['gpiozero'] = _gpio_mod

# Voice / audio hardware
sys.modules['pvporcupine'] = MagicMock()
sys.modules['pvrhino'] = MagicMock()
sys.modules['sounddevice'] = MagicMock()
sys.modules['faster_whisper'] = MagicMock()

# Patch DisplayRenderer before main imports it
import display.renderer as _dr
_dr.DisplayRenderer = SandboxRenderer

# Patch AudioPlayer, TTS, VoiceController before main imports them
import audio.player as _ap
_ap.AudioPlayer = FakeAudioPlayer

import audio.tts as _atts
_atts.create_tts = lambda: FakeTTS()

import audio.voice as _av
_av.VoiceController = FakeVoice

# ── 4. Now safe to import the full app ───────────────────────────────────────
from main import MainController  # noqa: E402  (imports must be after patches)


# ── 5. The Sandbox class ──────────────────────────────────────────────────────

class Sandbox:
    """
    Wraps a real MainController (full app) with mocked hardware.

    After every input method the display is re-rendered and a screenshot
    is auto-saved to /tmp/sb_<step>.png unless you pass auto_ss=False.
    Call sb.ss("label") any time to save a named screenshot.

    Navigation convention (matches physical device):
        nav(+n)  → scroll DOWN / next item   (encoder counter-clockwise)
        nav(-n)  → scroll UP  / prev item    (encoder clockwise)
    """

    SCREENSHOT_DIR = "/tmp"

    def __init__(self, auto_ss: bool = True):
        self.auto_ss = auto_ss
        self._step = 0
        self._last_path = None

        print("[Sandbox] Initializing MainController...")
        os.chdir(str(_ROOT))  # ensure relative paths (recipes/, assets/) resolve

        # Suppress verbose init output
        self._ctrl = MainController()

        # Stash handy shortcuts
        self._enc = self._ctrl.encoder            # FakeRotaryEncoder
        self._btn = self._ctrl.encoder_button     # FakeButton
        self._vol_up_btn = self._ctrl.button_up   # FakeButton
        self._vol_dn_btn = self._ctrl.button_down # FakeButton
        self._music = self._ctrl.music_app
        self._spotify = self._ctrl.spotify

        print("[Sandbox] Ready. Call sb.help() for commands.")
        self._do_render()
        if auto_ss:
            self.ss("init")

    # ── Input methods ──────────────────────────────────────────────────────────

    def nav(self, steps: int = 1):
        """Rotate encoder. Positive = scroll down/next. Negative = up/prev."""
        for _ in range(abs(steps)):
            if steps > 0:
                self._enc.rotate_ccw()   # CCW = direction +1 = scroll down
            else:
                self._enc.rotate_cw()    # CW  = direction -1 = scroll up
        self._settle()

    def press(self):
        """Press + release encoder button (select / back)."""
        # Clear state-change cooldown so rapid synthetic presses all register
        # (real hardware needs 300ms between presses; sandbox doesn't)
        self._ctrl._last_state_change = 0.0
        self._btn.trigger_press()
        time.sleep(0.02)
        self._btn.trigger_release()
        # Also clear the encoder-bounce filter — synthetic inputs have no bounce
        self._ctrl._last_encoder_press_time = 0.0
        self._settle()

    def hold(self):
        """Hold encoder button (always goes to home)."""
        self._ctrl._last_state_change = 0.0
        self._btn.trigger_hold()
        self._ctrl._last_encoder_press_time = 0.0
        self._settle()

    def vol_up(self, times: int = 1):
        """Press volume-up button N times."""
        for _ in range(times):
            self._vol_up_btn.trigger_press()
        self._settle()

    def vol_dn(self, times: int = 1):
        """Press volume-down button N times."""
        for _ in range(times):
            self._vol_dn_btn.trigger_press()
        self._settle()

    def wait(self, ms: int = 500):
        """Wait N milliseconds (lets background threads update state)."""
        time.sleep(ms / 1000)
        self._do_render()
        if self.auto_ss:
            self.ss(f"wait_{ms}ms")

    # ── Screenshot / state ─────────────────────────────────────────────────────

    def ss(self, label: str = "") -> str:
        """Save a screenshot. Returns the file path."""
        if not label:
            label = f"step{self._step:03d}"
        self._step += 1
        path = f"{self.SCREENSHOT_DIR}/sb_{label}.png"
        self._ctrl.renderer.save(path)
        self._last_path = path

        # State summary on every screenshot
        c = self._ctrl
        m = self._music
        app_s = c.state.value
        mus_s = m.music_state.value if hasattr(m, 'music_state') else "?"
        track = f" | {m.track_name[:30]!r}" if m.track_name else ""
        playing = " ▶" if m.is_playing else (" ⏸" if m.track_name else "")
        print(f"  [ss] {path}  app={app_s}  music={mus_s}{track}{playing}")
        return path

    def state(self):
        """Print full state summary."""
        c = self._ctrl
        m = self._music
        print(f"App state       : {c.state.value}")
        print(f"Music state     : {m.music_state.value}")
        print(f"Track           : {m.track_name!r}")
        print(f"Artist          : {m.artist_name!r}")
        print(f"Is playing      : {m.is_playing}")
        print(f"_force_api_poll : {m._force_api_poll}")
        print(f"Selected index  : {m._selected_index}")
        print(f"List items      : {len(m._list_items)} items loaded")
        if m._list_items:
            for i, item in enumerate(m._list_items[:5]):
                print(f"  [{i}] {item.get('name','?')}")
            if len(m._list_items) > 5:
                print(f"  ... {len(m._list_items)-5} more")
        if hasattr(c, '_last_encoder_press_time'):
            age = time.time() - c._last_encoder_press_time
            print(f"Encoder press   : {age:.2f}s ago")

    def spotify_state(self):
        """Check live Spotify API — devices + current playback."""
        try:
            sp = self._spotify._sp
            if not sp:
                print("Spotify not available")
                return
            print("=== Spotify Devices ===")
            devices = sp.devices()
            for d in devices.get("devices", []):
                active = "ACTIVE" if d.get("is_active") else "inactive"
                print(f"  [{active}] {d.get('name')} vol={d.get('volume_percent')}%")
            if not devices.get("devices"):
                print("  (no devices)")
            print("\n=== Current Playback ===")
            pb = sp.current_playback()
            if pb and pb.get("item"):
                item = pb["item"]
                artists = ", ".join(a["name"] for a in item.get("artists", []))
                pos = pb.get("progress_ms", 0) // 1000
                dur = item.get("duration_ms", 0) // 1000
                sym = "▶" if pb.get("is_playing") else "⏸"
                print(f"  {sym} {item['name']} — {artists}")
                print(f"     {pos//60}:{pos%60:02d} / {dur//60}:{dur%60:02d}")
            else:
                print("  (no active playback)")
        except Exception as e:
            print(f"  Spotify error: {e}")

    def help(self):
        """Print available commands."""
        print("""
Sandbox commands:
  sb.nav(n)          scroll down n (negative = up); like rotating encoder
  sb.press()         encoder button press + release
  sb.hold()          encoder hold (→ home)
  sb.vol_up(n)       volume up button n times
  sb.vol_dn(n)       volume down button n times
  sb.wait(ms)        wait N ms + render (lets background threads run)
  sb.ss("label")     screenshot → /tmp/sb_label.png
  sb.state()         print detailed app state
  sb.spotify_state() live Spotify API status
  sb.help()          this message
""")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _do_render(self):
        """Force a render, bypassing cooldown and debounce."""
        c = self._ctrl
        # Cancel pending debounce timer
        with c._lock:
            if c._debounce_timer:
                c._debounce_timer.cancel()
                c._debounce_timer = None
        c._pending_update.clear()
        # Reset cooldown so the render isn't skipped
        c._full_refresh_cooldown_until = 0
        # Render
        c._render()

    def _settle(self):
        """Short sleep so callbacks finish, then render + optional auto-screenshot."""
        time.sleep(0.05)
        self._do_render()
        if self.auto_ss:
            self.ss()


# ── 6. Built-in scenarios ─────────────────────────────────────────────────────

def _go_to_music(sb: "Sandbox"):
    """Helper: navigate from home to the music app menu."""
    sb.press()      # home → main menu
    sb.nav(2)       # index 0 (Recipes) → index 2 (Music)
    sb.press()      # → music app (MENU state)


def demo_menu_navigation():
    """Navigate through the main menu."""
    print("\n=== DEMO: Main menu navigation ===\n")
    sb = Sandbox(auto_ss=False)

    sb.ss("home")
    sb.press()                    # → main menu
    sb.ss("menu_recipes_sel")
    sb.nav(1); sb.ss("menu_timers_sel")
    sb.nav(1); sb.ss("menu_music_sel")
    sb.hold()                     # → home
    sb.ss("home_again")
    print("\n=== Done. Screenshots in /tmp/sb_*.png ===")
    return sb


def demo_music_menu():
    """Enter the music app and browse menus."""
    print("\n=== DEMO: Music app menu ===\n")
    sb = Sandbox(auto_ss=False)

    sb.ss("home")
    _go_to_music(sb)
    sb.ss("music_menu")

    # Scroll through music menu items
    sb.nav(1); sb.ss("music_nav1")   # Playlists highlighted
    sb.nav(1); sb.ss("music_nav2")
    sb.nav(-1); sb.ss("music_back_to_top")

    # Open playlists — navigate back to index 0
    sb.nav(-2)                        # back to "My Playlists" (index 0)
    sb.press()                        # → PLAYLISTS (loads from Spotify)
    sb.ss("playlists_loading")

    print("  Waiting 3s for playlist list to load from Spotify...")
    sb.wait(3000)
    sb.ss("playlists_loaded")
    sb.state()
    return sb


def demo_bug_playlist_skip():
    """
    Reproduce and verify the fix for the playlist→'Starting...' bug.

    The original bug: pressing encoder to select a playlist also generated
    spurious rotate events (mechanical bounce) that immediately fired
    next_track() / previous_track(), causing 404 errors.

    The fix: _last_encoder_press_time in main.py blocks rotations for
    400ms after any button press/release.

    This demo:
      1. Navigates to a playlist and selects it (real Spotify call)
      2. Manually simulates the bounce rotations WITHOUT the fix active
         → shows skip commands firing (bug reproduced)
      3. Restores the fix and does it again
         → shows skips blocked (bug fixed)
    """
    print("\n=== DEMO: Playlist selection + encoder bounce fix ===\n")
    sb = Sandbox(auto_ss=False)

    _go_to_music(sb)
    # Index 0 = "My Playlists" (selected by default, no nav needed)
    sb.press()      # → PLAYLISTS
    sb.ss("playlists_loading")

    print("  Waiting 4s for Spotify playlists to load...")
    sb.wait(4000)
    sb.ss("playlists_loaded")
    sb.state()

    if not sb._music._list_items:
        print("  No playlists loaded — check Spotify connection")
        sb.spotify_state()
        return sb

    # Select first playlist
    sb.nav(1)       # highlight first playlist (index 1; index 0 = Back)
    sb.ss("first_playlist_highlighted")

    # ── Part 1: Show what the BUG looked like ────────────────────────────────
    print("\n  [Part 1: Simulating the OLD bug (no bounce filter)]")
    # Manually fire press callbacks like the hardware would
    sb._ctrl._last_encoder_press_time = 0.0   # disable filter to show old bug
    sb._btn.trigger_press()
    time.sleep(0.02)
    sb._btn.trigger_release()
    sb._ctrl._last_encoder_press_time = 0.0   # keep filter off

    # Simulate mechanical bounce immediately after release
    print("  [Bounce: rotate_ccw → would trigger next_track]")
    sb._enc.rotate_ccw()   # skip → next (BUG)
    time.sleep(0.01)
    print("  [Bounce: rotate_cw → would trigger previous_track]")
    sb._enc.rotate_cw()    # skip → prev (BUG)
    sb._do_render()
    sb.ss("bug_now_playing_starting")
    sb.state()

    # ── Part 2: Show the FIX ─────────────────────────────────────────────────
    print("\n  [Part 2: Same scenario WITH the bounce fix active]")
    # Go back to playlists
    sb.press()      # NOW_PLAYING → MENU
    sb.nav(1)       # navigate to Playlists
    sb.press()      # → PLAYLISTS
    sb.wait(1000)
    sb.nav(1)       # highlight first playlist again

    # Now press with the fix ACTIVE (press() resets timer to 0, but we restore it)
    sb._btn.trigger_press()
    time.sleep(0.02)
    sb._btn.trigger_release()
    # Fix is active: _last_encoder_press_time is recent (set by trigger_release)
    # Do NOT reset it — leave the bounce guard in place

    print("  [Bounce with fix: rotate_ccw → should be BLOCKED]")
    sb._enc.rotate_ccw()   # should be blocked by 400ms guard
    time.sleep(0.01)
    print("  [Bounce with fix: rotate_cw → should be BLOCKED]")
    sb._enc.rotate_cw()    # should be blocked
    sb._ctrl._last_encoder_press_time = 0.0   # now clear it so render works
    sb._do_render()
    sb.ss("fix_now_playing_starting")
    sb.state()

    print("\n  [Waiting 4s for API poll to populate track info...]")
    sb.wait(4000)
    sb.ss("after_api_poll_track_info")
    sb.state()

    return sb


DEMOS = {
    "menu_navigation": demo_menu_navigation,
    "music_menu": demo_music_menu,
    "bug_playlist_skip": demo_bug_playlist_skip,
}


# ── 7. CLI ────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        print("Run with 'python -i scripts/sandbox.py' for interactive mode.")
        print(f"Available demos: {', '.join(DEMOS)}")
        return

    if args[0] == "run" and len(args) > 1:
        script_path = args[1]
        print(f"[Sandbox] Running scenario: {script_path}")
        sb = Sandbox(auto_ss=False)
        with open(script_path) as f:
            code = f.read()
        exec(code, {"sb": sb, "time": time})  # noqa: S102

    elif args[0] == "demo":
        name = args[1] if len(args) > 1 else "menu_navigation"
        if name not in DEMOS:
            print(f"Unknown demo: {name}. Available: {', '.join(DEMOS)}")
            sys.exit(1)
        DEMOS[name]()

    else:
        print(f"Unknown command: {args[0]}")
        print(__doc__)


if __name__ == "__main__":
    main()

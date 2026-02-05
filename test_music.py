#!/usr/bin/env python3
"""Test script for music app with Spotify Connect integration."""

import json
import time
import threading
from pathlib import Path
from gpiozero import RotaryEncoder, Button

from display.renderer import DisplayRenderer
from apps.music import MusicApp, STATE_FILE
from config import (
    ENCODER_PIN_A, ENCODER_PIN_B, ENCODER_PIN_SW,
    DISPLAY_WIDTH, DISPLAY_HEIGHT
)

# Debounce settings
DEBOUNCE_DELAY = 0.25  # 250ms debounce


def create_test_state(track_name: str = None, artist: str = None, album: str = None,
                      is_playing: bool = True, position_ms: int = 0, duration_ms: int = 180000,
                      cover_url: str = ""):
    """Create a test state file for development without Spotify."""
    from datetime import datetime

    state = {
        "last_update": datetime.now().isoformat(),
        "event": "track_changed" if track_name else "stopped",
        "is_playing": is_playing,
        "position_ms": position_ms,
        "volume": 75,
        "connected": track_name is not None,
    }

    if track_name:
        state["track"] = {
            "name": track_name,
            "artists": artist or "Unknown Artist",
            "album": album or "Unknown Album",
            "duration_ms": duration_ms,
            "cover_url": cover_url,
            "is_explicit": False,
        }

    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"  [Created test state: {track_name or 'No track'}]")


def main():
    print("Initializing display...")
    renderer = DisplayRenderer()
    # First do a full init and clear to remove any residue
    renderer.init()
    renderer.clear()
    # Then switch to partial mode
    renderer.init_partial()

    print("Creating music app...")
    app = MusicApp(renderer)

    # Debounce state
    pending_update = threading.Event()
    debounce_timer = None
    lock = threading.Lock()

    def schedule_update():
        """Schedule a debounced update."""
        nonlocal debounce_timer
        with lock:
            if debounce_timer is not None:
                debounce_timer.cancel()
            debounce_timer = threading.Timer(DEBOUNCE_DELAY, do_update)
            debounce_timer.start()

    def do_update():
        """Perform the actual update."""
        pending_update.set()

    # Set callback for state updates (full redraw)
    app.on_update = schedule_update

    # Set callback for progress updates (partial refresh)
    def on_progress():
        print("  [Progress update - partial refresh]")
        app.update_progress()

    app.on_progress_update = on_progress

    # Initial render
    print("Rendering initial view...")
    app.render()
    buf = renderer.epd.getbuffer(renderer.framebuffer)
    renderer.epd.display_Partial(buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)

    # Set up encoder
    print("Setting up encoder...")
    encoder = RotaryEncoder(ENCODER_PIN_A, ENCODER_PIN_B, max_steps=0)
    button = Button(ENCODER_PIN_SW, hold_time=1.0)

    # Track button state to distinguish press from hold
    button_held = [False]

    def on_cw():
        print("  [Encoder CW - navigate]")
        app.navigate(1)
        schedule_update()

    def on_ccw():
        print("  [Encoder CCW - navigate]")
        app.navigate(-1)
        schedule_update()

    def on_button_press():
        button_held[0] = False

    def on_button_release():
        # Only trigger select if it wasn't a hold
        if not button_held[0]:
            print("  [Button press - select (no-op, use Spotify app)]")
            app.select()

    def on_hold():
        button_held[0] = True
        print("  [Button hold - back (would exit to menu)]")
        result = app.back()
        print(f"  [back() returned: {result}]")

    encoder.when_rotated_clockwise = on_ccw  # Swapped per user preference
    encoder.when_rotated_counter_clockwise = on_cw
    button.when_pressed = on_button_press
    button.when_released = on_button_release
    button.when_held = on_hold

    print("\n" + "="*50)
    print("MUSIC APP TEST")
    print("="*50)
    print("Controls:")
    print("  - Turn encoder: (reserved for future volume control)")
    print("  - Press encoder: (no-op, control from Spotify app)")
    print("  - Hold encoder: Would go back to menu")
    print("\nTo test with mock data, press 't' then Enter")
    print("To clear mock data, press 'c' then Enter")
    print("Test duration: 3 minutes")
    print("="*50 + "\n")

    # Check if state file exists
    if STATE_FILE.exists():
        print(f"Found existing state file: {STATE_FILE}")
    else:
        print("No state file found. Play music on Spotify and select 'Kitchen Display'")
        print("Or press 't' + Enter to create test data")

    # Thread for keyboard input
    test_mode = [False]

    def keyboard_listener():
        import sys
        import select
        while True:
            # Non-blocking check for input
            if select.select([sys.stdin], [], [], 0.5)[0]:
                cmd = sys.stdin.readline().strip().lower()
                if cmd == 't':
                    # Create test data with album cover
                    # Using a public domain image for testing
                    create_test_state(
                        track_name="Surface Pressure",
                        artist="Jessica Darrow",
                        album="Encanto (Original Motion Picture Soundtrack)",
                        is_playing=True,
                        position_ms=45000,
                        duration_ms=205000,
                        cover_url="https://i.scdn.co/image/ab67616d0000b273dcba50c72ede56f19999977c"
                    )
                    schedule_update()
                elif cmd == 'c':
                    # Clear state
                    if STATE_FILE.exists():
                        STATE_FILE.unlink()
                        print("  [Cleared state file]")
                    schedule_update()
                elif cmd == 'p':
                    # Toggle play/pause in test state
                    if STATE_FILE.exists():
                        state = json.loads(STATE_FILE.read_text())
                        state["is_playing"] = not state.get("is_playing", False)
                        state["event"] = "playing" if state["is_playing"] else "paused"
                        from datetime import datetime
                        state["last_update"] = datetime.now().isoformat()
                        STATE_FILE.write_text(json.dumps(state, indent=2))
                        print(f"  [Toggled to: {'playing' if state['is_playing'] else 'paused'}]")
                        schedule_update()

    kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
    kb_thread.start()

    # Run for 3 minutes
    start_time = time.time()
    duration = 180  # 3 minutes

    try:
        while time.time() - start_time < duration:
            remaining = int(duration - (time.time() - start_time))
            track = app.track_name if app.track_name else "No track"
            status = "Playing" if app.is_playing else "Paused" if app.track_name else "Idle"
            print(f"\rTime: {remaining}s | {status}: {track[:30]}   ", end="", flush=True)

            # Check for pending update
            if pending_update.wait(timeout=0.5):
                pending_update.clear()

                # Render and display using partial mode
                app.render()
                buf = renderer.epd.getbuffer(renderer.framebuffer)
                renderer.epd.display_Partial(buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
                print(f"\n  [Display updated]")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")

    print("\n\nTest complete!")
    print("Cleaning up...")
    app.shutdown()
    renderer.clear()
    print("Done!")


if __name__ == "__main__":
    main()

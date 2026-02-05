#!/usr/bin/env python3
"""Test script for timer app with encoder input."""

import time
import threading
from gpiozero import RotaryEncoder, Button

from display.renderer import DisplayRenderer
from audio.player import AudioPlayer
from apps.timers import TimerApp, TimerState
from config import (
    ENCODER_PIN_A, ENCODER_PIN_B, ENCODER_PIN_SW,
    DISPLAY_WIDTH, DISPLAY_HEIGHT
)

# Debounce settings
DEBOUNCE_DELAY = 0.25  # 250ms debounce


def main():
    print("Initializing display...")
    renderer = DisplayRenderer()
    # First do a full init and clear to remove any residue
    renderer.init()
    renderer.clear()
    # Then switch to partial mode
    renderer.init_partial()

    print("Initializing audio...")
    audio = AudioPlayer()

    print("Creating timer app...")
    app = TimerApp(renderer, audio)

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

    # Set callback for timer tick updates
    app.on_update = schedule_update

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
    press_time = [0]

    def on_cw():
        print("  [Encoder CW - navigate down]")
        app.navigate(1)
        schedule_update()

    def on_ccw():
        print("  [Encoder CCW - navigate up]")
        app.navigate(-1)
        schedule_update()

    def on_button_press():
        press_time[0] = time.time()
        button_held[0] = False

    def on_button_release():
        # Only trigger select if it wasn't a hold
        if not button_held[0]:
            print("  [Button press - select]")
            app.select()
            schedule_update()

    def on_hold():
        button_held[0] = True
        print("  [Button hold - back]")
        app.back()
        schedule_update()

    encoder.when_rotated_clockwise = on_ccw  # Swapped per user preference
    encoder.when_rotated_counter_clockwise = on_cw
    button.when_pressed = on_button_press
    button.when_released = on_button_release
    button.when_held = on_hold

    print("\n" + "="*50)
    print("TIMER APP TEST")
    print("="*50)
    print("Controls:")
    print("  - Turn encoder: Navigate / Adjust minutes")
    print("  - Press encoder: Select / Start timer / Dismiss alarm")
    print("  - Hold encoder: Go back")
    print("\nTest duration: 5 minutes")
    print("="*50 + "\n")

    # Run for 5 minutes
    start_time = time.time()
    duration = 300  # 5 minutes

    try:
        while time.time() - start_time < duration:
            remaining = int(duration - (time.time() - start_time))
            print(f"\rTime remaining: {remaining}s   ", end="", flush=True)

            # Check for pending update
            if pending_update.wait(timeout=0.1):
                pending_update.clear()

                # Render and display using partial mode
                app.render()
                buf = renderer.epd.getbuffer(renderer.framebuffer)
                renderer.epd.display_Partial(buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
                print(f"\n  [Display updated - state: {app.state.name}]")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")

    print("\n\nTest complete!")
    print("Cleaning up...")
    app.shutdown()
    audio.shutdown()
    renderer.clear()
    print("Done!")


if __name__ == "__main__":
    main()

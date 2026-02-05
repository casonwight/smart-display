#!/usr/bin/env python3
"""Test the menu screen app with encoder input."""

import sys
import time
from pathlib import Path

# Ensure we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from display.renderer import DisplayRenderer
from apps.menu import MenuApp
from input.encoder import RotaryEncoder


def main():
    print("Initializing display...")
    renderer = DisplayRenderer()
    renderer.init()

    print("Creating menu app...")
    menu = MenuApp(renderer)

    print("Rendering menu...")
    menu.render()
    renderer.full_refresh()

    print()
    print("Menu displayed!")
    print("Use the rotary encoder to navigate:")
    print("  - Rotate: Move selection")
    print("  - Press: Select (prints selection)")
    print("  - Long press: Exit")
    print()

    # Set up encoder
    print("Initializing encoder...")
    encoder = RotaryEncoder()

    # Track if we should exit
    running = True

    def on_rotate_cw():
        menu.navigate(1)
        menu.render()
        renderer.full_refresh()
        print(f"Selected: {menu.get_selected().name}")

    def on_rotate_ccw():
        menu.navigate(-1)
        menu.render()
        renderer.full_refresh()
        print(f"Selected: {menu.get_selected().name}")

    def on_press():
        selected = menu.select()
        print(f">>> SELECTED: {selected.name} <<<")

    def on_long_press():
        nonlocal running
        print("Long press - exiting...")
        running = False

    encoder.on_rotate_cw = on_rotate_cw
    encoder.on_rotate_ccw = on_rotate_ccw
    encoder.on_press = on_press
    encoder.on_long_press = on_long_press

    print("Ready! Rotate encoder to navigate, press to select, long-press to exit.")

    try:
        while running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nKeyboard interrupt")

    print("Cleaning up...")
    encoder.close()
    renderer.sleep()
    print("Done!")


if __name__ == "__main__":
    main()

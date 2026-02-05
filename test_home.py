#!/usr/bin/env python3
"""Test the home screen app."""

import sys
import time
from pathlib import Path

# Ensure we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from display.renderer import DisplayRenderer
from apps.home import HomeApp


def main():
    print("Initializing display...")
    renderer = DisplayRenderer()
    renderer.init()

    print("Creating home app...")
    home = HomeApp(renderer)

    print("Rendering home screen (full screen with time overlay)...")
    home.render()

    print("Doing full refresh...")
    renderer.full_refresh()

    print()
    print("Home screen displayed!")
    print("You should see:")
    print("  - Full screen otter wallpaper")
    print("  - Time in bottom left with white cloud background")
    print()


if __name__ == "__main__":
    main()

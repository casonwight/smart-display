#!/usr/bin/env python3
"""Test the display renderer with a test pattern."""

import sys
from pathlib import Path

# Ensure we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from display.renderer import DisplayRenderer


def main():
    print("Initializing display...")
    renderer = DisplayRenderer()

    print("Showing test pattern...")
    renderer.show_test_pattern()

    print("Test pattern displayed!")
    print("You should see:")
    print("  - Border around the entire screen")
    print("  - Three regions: HEADER, CONTENT, FOOTER")
    print("  - Corner markers: TL, TR, BL, BR")
    print()
    print("Press Enter to clear and sleep...")
    input()

    print("Clearing display...")
    renderer.clear()

    print("Putting display to sleep...")
    renderer.sleep()

    print("Done!")


if __name__ == "__main__":
    main()

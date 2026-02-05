#!/usr/bin/env python3
"""Test input handling - shows feedback on display."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from display.renderer import DisplayRenderer
from input.encoder import RotaryEncoder
from input.buttons import VolumeButtons
from PIL import Image, ImageDraw, ImageFont


def main():
    print("Initializing display...")
    renderer = DisplayRenderer()
    renderer.init()
    renderer.clear()

    print("Initializing inputs...")
    encoder = RotaryEncoder()
    buttons = VolumeButtons()

    # State
    state = {
        "position": 0,
        "last_action": "None",
        "volume": 50,
    }

    # Load font
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except:
        font = ImageFont.load_default()
        small_font = font

    def update_display():
        """Redraw the display with current state."""
        img = Image.new('1', (800, 480), 1)
        draw = ImageDraw.Draw(img)

        # Title
        draw.text((250, 20), "Input Test", font=font, fill=0)

        # Encoder position
        draw.text((100, 100), f"Encoder Position: {state['position']}", font=font, fill=0)

        # Last action
        draw.text((100, 180), f"Last Action: {state['last_action']}", font=font, fill=0)

        # Volume
        draw.text((100, 260), f"Volume: {state['volume']}%", font=font, fill=0)

        # Volume bar
        bar_x = 100
        bar_y = 320
        bar_width = 600
        bar_height = 40
        draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + bar_height], outline=0, width=2)
        fill_width = int((state['volume'] / 100) * bar_width)
        if fill_width > 0:
            draw.rectangle([bar_x, bar_y, bar_x + fill_width, bar_y + bar_height], fill=0)

        # Instructions
        draw.text((150, 420), "Rotate encoder, press buttons. Ctrl+C to exit.", font=small_font, fill=0)

        renderer.framebuffer = img
        renderer.full_refresh()

    def on_cw():
        state['position'] += 1
        state['last_action'] = "Rotate CW"
        print(f"CW - Position: {state['position']}")
        update_display()

    def on_ccw():
        state['position'] -= 1
        state['last_action'] = "Rotate CCW"
        print(f"CCW - Position: {state['position']}")
        update_display()

    def on_press():
        state['last_action'] = "Encoder Press"
        print("Encoder pressed!")
        update_display()

    def on_long_press():
        state['last_action'] = "Encoder LONG Press"
        print("Encoder LONG press!")
        update_display()

    def on_vol_up():
        state['volume'] = min(100, state['volume'] + 5)
        state['last_action'] = "Volume UP"
        print(f"Volume UP: {state['volume']}%")
        update_display()

    def on_vol_down():
        state['volume'] = max(0, state['volume'] - 5)
        state['last_action'] = "Volume DOWN"
        print(f"Volume DOWN: {state['volume']}%")
        update_display()

    # Register callbacks
    encoder.on_rotate_cw = on_cw
    encoder.on_rotate_ccw = on_ccw
    encoder.on_press = on_press
    encoder.on_long_press = on_long_press
    buttons.on_volume_up(on_vol_up)
    buttons.on_volume_down(on_vol_down)

    # Initial display
    update_display()

    print("\nInput test running!")
    print("- Rotate encoder to change position")
    print("- Press encoder for short/long press")
    print("- Press buttons to change volume")
    print("- Press Ctrl+C to exit\n")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nExiting...")
        encoder.close()
        buttons.close()
        renderer.clear()
        renderer.sleep()
        print("Done!")


if __name__ == "__main__":
    main()

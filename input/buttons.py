from gpiozero import Button
from typing import Callable, Optional

from config import BUTTON_UP_PIN, BUTTON_DOWN_PIN


class VolumeButtons:
    """Handles the two volume control buttons."""

    def __init__(self, pin_up: int = BUTTON_UP_PIN, pin_down: int = BUTTON_DOWN_PIN):
        self.btn_up = Button(pin_up, pull_up=True, bounce_time=0.05)
        self.btn_down = Button(pin_down, pull_up=True, bounce_time=0.05)

        self._on_volume_up: Optional[Callable] = None
        self._on_volume_down: Optional[Callable] = None

    def on_volume_up(self, callback: Callable):
        """Register callback for volume up button press."""
        self._on_volume_up = callback
        self.btn_up.when_pressed = callback

    def on_volume_down(self, callback: Callable):
        """Register callback for volume down button press."""
        self._on_volume_down = callback
        self.btn_down.when_pressed = callback

    def close(self):
        """Clean up resources."""
        self.btn_up.close()
        self.btn_down.close()

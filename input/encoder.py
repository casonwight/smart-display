from gpiozero import RotaryEncoder as GPIOEncoder, Button
from typing import Callable, Optional
import time
import threading

from config import ENCODER_PIN_A, ENCODER_PIN_B, ENCODER_PIN_SW


class RotaryEncoder:
    """Handles rotary encoder input with rotation and button press detection."""

    def __init__(self, pin_a: int = ENCODER_PIN_A, pin_b: int = ENCODER_PIN_B,
                 pin_sw: int = ENCODER_PIN_SW):
        # Set up encoder for rotation
        self.encoder = GPIOEncoder(pin_a, pin_b, max_steps=0)

        # Set up button with pull-up
        self.button = Button(pin_sw, pull_up=True, bounce_time=0.05)

        # Callbacks
        self._on_rotate_cw: Optional[Callable] = None
        self._on_rotate_ccw: Optional[Callable] = None
        self._on_press: Optional[Callable] = None
        self._on_long_press: Optional[Callable] = None

        # Long press detection
        self.long_press_time = 1.0  # seconds
        self._press_start: float = 0
        self._long_press_fired = False

        # Track last position for step detection
        self._last_steps = 0

        # Set up button callbacks
        self.button.when_pressed = self._handle_press
        self.button.when_released = self._handle_release

        # Start rotation monitoring thread
        self._running = True
        self._rotation_thread = threading.Thread(target=self._monitor_rotation, daemon=True)
        self._rotation_thread.start()

    def _monitor_rotation(self):
        """Monitor encoder rotation in background thread."""
        while self._running:
            current = self.encoder.steps
            diff = current - self._last_steps

            if diff != 0:
                self._last_steps = current
                if diff > 0 and self._on_rotate_cw:
                    for _ in range(diff):
                        self._on_rotate_cw()
                elif diff < 0 and self._on_rotate_ccw:
                    for _ in range(abs(diff)):
                        self._on_rotate_ccw()

            time.sleep(0.01)  # 10ms polling

    def _handle_press(self):
        """Handle button press start."""
        self._press_start = time.time()
        self._long_press_fired = False

        # Start long press detection in background
        threading.Thread(target=self._check_long_press, daemon=True).start()

    def _check_long_press(self):
        """Check for long press after delay."""
        time.sleep(self.long_press_time)
        if self.button.is_pressed and not self._long_press_fired:
            self._long_press_fired = True
            if self._on_long_press:
                self._on_long_press()

    def _handle_release(self):
        """Handle button release."""
        if not self._long_press_fired:
            # Short press
            if self._on_press:
                self._on_press()

    @property
    def on_rotate_cw(self) -> Optional[Callable]:
        return self._on_rotate_cw

    @on_rotate_cw.setter
    def on_rotate_cw(self, callback: Callable):
        self._on_rotate_cw = callback

    @property
    def on_rotate_ccw(self) -> Optional[Callable]:
        return self._on_rotate_ccw

    @on_rotate_ccw.setter
    def on_rotate_ccw(self, callback: Callable):
        self._on_rotate_ccw = callback

    @property
    def on_press(self) -> Optional[Callable]:
        return self._on_press

    @on_press.setter
    def on_press(self, callback: Callable):
        self._on_press = callback

    @property
    def on_long_press(self) -> Optional[Callable]:
        return self._on_long_press

    @on_long_press.setter
    def on_long_press(self, callback: Callable):
        self._on_long_press = callback

    def get_steps(self) -> int:
        """Get accumulated rotation steps (positive = CW, negative = CCW)."""
        steps = self.encoder.steps - self._last_steps
        self._last_steps = self.encoder.steps
        return steps

    def close(self):
        """Clean up resources."""
        self._running = False
        self.encoder.close()
        self.button.close()

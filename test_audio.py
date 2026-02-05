#!/usr/bin/env python3
"""Test audio player - plays beeps through speakers."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from audio.player import AudioPlayer


def main():
    print("Initializing audio player...")
    player = AudioPlayer()

    print("\n1. Playing soft UI beep...")
    player.soft_beep()
    time.sleep(0.3)

    print("2. Playing chime (880Hz)...")
    player._play_chime(frequency=880, duration=0.8, volume=0.6)
    time.sleep(0.3)

    print("3. Playing lower chime (523Hz - C5)...")
    player._play_chime(frequency=523, duration=0.8, volume=0.6)
    time.sleep(0.3)

    print("4. Playing timer alarm pattern (3 chimes)...")
    player.timer_alarm(repeats=3)
    time.sleep(0.3)

    print("\nDone! Did you hear the sounds?")


if __name__ == "__main__":
    main()

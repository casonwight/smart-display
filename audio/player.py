import subprocess
import os
import math
import wave
import struct
import signal
from pathlib import Path
from typing import Optional

from config import AUDIO_DEVICE, DEFAULT_VOLUME, VOLUME_STEP, AMP_SD_PIN_1, AMP_SD_PIN_2, I2S_BCLK_PIN


class AudioPlayer:
    """Handles audio playback and volume control."""

    def __init__(self, device: str = AUDIO_DEVICE):
        self.device = device
        self._volume = DEFAULT_VOLUME
        self._muted = False
        self._current_process: Optional[subprocess.Popen] = None

        # Ensure audio hardware is enabled
        self._init_hardware()
        # Set initial system volume
        self._set_system_volume(self._volume)

    def _init_hardware(self):
        """Initialize audio hardware (I2S pins and amp SD pins)."""
        try:
            # Enable I2S BCLK pin
            subprocess.run(
                ["pinctrl", "set", str(I2S_BCLK_PIN), "a2"],
                check=True, capture_output=True
            )
            # Enable both amp SD pins (high = on)
            subprocess.run(
                ["pinctrl", "set", f"{AMP_SD_PIN_1},{AMP_SD_PIN_2}", "op", "dh"],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not initialize audio hardware: {e}")

    @property
    def volume(self) -> int:
        return self._volume

    @volume.setter
    def volume(self, value: int):
        self._volume = max(0, min(100, value))
        # Also set system volume via ALSA so it affects Spotify
        self._set_system_volume(self._volume)

    def sync_volume_from_system(self):
        """Read actual system volume and sync internal state (for when phone changes it)."""
        try:
            result = subprocess.run(
                ["amixer", "-c", "2", "sget", "SoftMaster"],
                capture_output=True, text=True, check=False
            )
            # Parse output like "Front Left: 128 [50%] [0.00dB]"
            for line in result.stdout.split('\n'):
                if '[' in line and '%]' in line:
                    # Extract percentage
                    start = line.find('[') + 1
                    end = line.find('%]')
                    if start > 0 and end > start:
                        pct = int(line[start:end])
                        self._volume = pct
                        return pct
        except Exception:
            pass
        return self._volume

    def _set_system_volume(self, volume_percent: int):
        """Set system volume via ALSA mixer."""
        try:
            subprocess.run(
                ["amixer", "-c", "2", "sset", "SoftMaster", f"{volume_percent}%"],
                capture_output=True, check=False
            )
        except Exception:
            pass

    def volume_up(self, step: int = VOLUME_STEP) -> int:
        """Increase volume by step amount."""
        # Sync from system first (in case phone changed it)
        self.sync_volume_from_system()
        self.volume = self._volume + step
        return self._volume

    def volume_down(self, step: int = VOLUME_STEP) -> int:
        """Decrease volume by step amount."""
        # Sync from system first (in case phone changed it)
        self.sync_volume_from_system()
        self.volume = self._volume - step
        return self._volume

    def mute(self):
        """Mute audio by pulling amp SD pins low."""
        self._muted = True
        try:
            subprocess.run(
                ["pinctrl", "set", f"{AMP_SD_PIN_1},{AMP_SD_PIN_2}", "op", "dl"],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError:
            pass

    def unmute(self):
        """Unmute audio by pulling amp SD pins high."""
        self._muted = False
        try:
            subprocess.run(
                ["pinctrl", "set", f"{AMP_SD_PIN_1},{AMP_SD_PIN_2}", "op", "dh"],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError:
            pass

    def toggle_mute(self) -> bool:
        """Toggle mute state. Returns new mute state."""
        if self._muted:
            self.unmute()
        else:
            self.mute()
        return self._muted

    def play_file(self, path: str, blocking: bool = True, volume_boost: float = 1.0):
        """Play an audio file (supports wav and mp3).

        Args:
            path: Path to audio file
            blocking: Wait for playback to complete
            volume_boost: Multiply audio volume (e.g., 3.0 = 3x louder for alarm over dimmed music)
        """
        if self._muted:
            return

        # Check file extension to determine player
        if path.lower().endswith('.mp3'):
            # Use ffmpeg to decode MP3, pipe to aplay with softvol device
            # Apply volume boost filter if needed (for alarm over dimmed music)
            volume_filter = f"-af volume={volume_boost}" if volume_boost != 1.0 else ""
            cmd = f'ffmpeg -i "{path}" {volume_filter} -f wav -acodec pcm_s16le -ar 48000 -ac 2 - 2>/dev/null | aplay -D softvol -q -'
            if blocking:
                subprocess.run(cmd, shell=True, capture_output=True)
            else:
                self._current_process = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        else:
            # Use aplay for wav files
            cmd = ["aplay", "-D", "softvol", path]

        if blocking:
            subprocess.run(cmd, capture_output=True)
        else:
            self._current_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop_playback(self):
        """Stop any currently playing audio."""
        if self._current_process and self._current_process.poll() is None:
            try:
                # Use SIGKILL directly
                self._current_process.kill()
                self._current_process.wait(timeout=1)
            except Exception:
                pass
            self._current_process = None

        # Kill any lingering audio processes (belt and suspenders)
        try:
            subprocess.run(["pkill", "-9", "ffmpeg"], capture_output=True)
            subprocess.run(["pkill", "-9", "aplay"], capture_output=True)
            subprocess.run(["pkill", "-9", "ffplay"], capture_output=True)
        except Exception:
            pass

    def beep(self, frequency: int = 880, duration_ms: int = 100, volume: float = 0.8):
        """Play a simple beep tone."""
        if self._muted:
            return

        # Generate tone and play it
        self._play_tone(frequency, duration_ms / 1000.0, volume)

    def soft_beep(self):
        """Play a soft UI feedback beep."""
        self.beep(frequency=880, duration_ms=100, volume=0.5)

    def timer_alarm(self, repeats: int = 3):
        """Play the timer alarm - pleasant chime pattern."""
        import time
        for i in range(repeats):
            self._play_chime(frequency=880, duration=0.6, volume=0.6)
            if i < repeats - 1:
                time.sleep(0.3)

    def _play_tone(self, frequency: int, duration: float, volume: float = 0.5):
        """Generate and play a simple sine wave tone."""
        self._generate_and_play(frequency, duration, volume, chime=False)

    def _play_chime(self, frequency: int, duration: float, volume: float = 0.5):
        """Generate and play a pleasant chime/bell sound with harmonics."""
        self._generate_and_play(frequency, duration, volume, chime=True)

    def _generate_and_play(self, frequency: int, duration: float, volume: float, chime: bool):
        """Generate and play a tone or chime."""
        sample_rate = 48000
        n_samples = int(sample_rate * duration)

        samples = []
        for i in range(n_samples):
            t = i / sample_rate

            if chime:
                # Bell/chime sound: exponential decay with harmonics
                decay = math.exp(-t * 3.0)  # Exponential decay

                # Fundamental + harmonics (bell-like overtones)
                sample = (
                    1.0 * math.sin(2 * math.pi * frequency * t) +          # Fundamental
                    0.5 * math.sin(2 * math.pi * frequency * 2.0 * t) +    # 2nd harmonic
                    0.25 * math.sin(2 * math.pi * frequency * 3.0 * t) +   # 3rd harmonic
                    0.15 * math.sin(2 * math.pi * frequency * 4.2 * t) +   # Inharmonic (bell-like)
                    0.1 * math.sin(2 * math.pi * frequency * 5.4 * t)      # Inharmonic
                )
                sample = volume * decay * sample / 2.0  # Normalize
            else:
                # Simple tone with envelope
                envelope = 1.0
                attack = 0.01
                release = 0.01
                if t < attack:
                    envelope = t / attack
                elif t > duration - release:
                    envelope = (duration - t) / release
                sample = volume * envelope * math.sin(2 * math.pi * frequency * t)

            sample_int = int(max(-32767, min(32767, sample * 32767)))
            samples.append(sample_int)

        # Write to temporary WAV file (stereo)
        tmp_path = "/tmp/beep.wav"
        with wave.open(tmp_path, 'w') as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            for sample in samples:
                wav.writeframes(struct.pack('<hh', sample, sample))

        self.play_file(tmp_path, blocking=True)

    def shutdown(self):
        """Shutdown audio (mute amps)."""
        self.mute()

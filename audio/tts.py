"""Text-to-speech module using standalone Piper binary for fast local TTS."""

import json
import os
import subprocess
import threading
from pathlib import Path

from config import MODELS_DIR, AUDIO_DEVICE


# Piper binary location (standalone binary, not Python package)
PIPER_BIN_DIR = Path.home() / "bin"
PIPER_BIN = PIPER_BIN_DIR / "piper"

# Piper model settings
PIPER_MODEL_DIR = Path(MODELS_DIR) / "piper"
PIPER_MODEL_NAME = "en_US-lessac-medium"  # Popular, natural US English voice
PIPER_MODEL_FILE = f"{PIPER_MODEL_NAME}.onnx"
PIPER_CONFIG_FILE = f"{PIPER_MODEL_NAME}.onnx.json"

# Download URLs (Hugging Face)
PIPER_MODEL_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/{PIPER_MODEL_FILE}"
PIPER_CONFIG_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/{PIPER_MODEL_FILE}.json"


class PiperTTS:
    """Fast local text-to-speech using standalone Piper binary."""

    def __init__(self):
        self._available = False
        self._speaking = False
        self._lock = threading.Lock()
        self._sample_rate = 22050  # Default, will read from config

        # Audio device (use plughw for format conversion)
        self._audio_device = AUDIO_DEVICE.replace("hw:", "plughw:") if AUDIO_DEVICE.startswith("hw:") else AUDIO_DEVICE

        # Environment for piper (needs library path)
        self._env = os.environ.copy()
        self._env["LD_LIBRARY_PATH"] = f"{PIPER_BIN_DIR}:{self._env.get('LD_LIBRARY_PATH', '')}"

        # Check for model files
        self.model_path = PIPER_MODEL_DIR / PIPER_MODEL_FILE
        self.config_path = PIPER_MODEL_DIR / PIPER_CONFIG_FILE

        # Create model directory if needed
        PIPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        # Download models if not present
        if not self.model_path.exists() or not self.config_path.exists():
            self._download_models()

        if self.model_path.exists() and self.config_path.exists():
            self._init_piper()
        else:
            print(f"    [Piper TTS: Model files not found]")
            print(f"    [Expected: {self.model_path}]")
            print(f"    [Expected: {self.config_path}]")

    def _download_models(self):
        """Download Piper model files."""
        import urllib.request

        print(f"    [Piper TTS: Downloading voice model...]")
        try:
            if not self.model_path.exists():
                print(f"    [Downloading {PIPER_MODEL_FILE}...]")
                urllib.request.urlretrieve(PIPER_MODEL_URL, self.model_path)

            if not self.config_path.exists():
                print(f"    [Downloading {PIPER_CONFIG_FILE}...]")
                urllib.request.urlretrieve(PIPER_CONFIG_URL, self.config_path)

            print(f"    [Piper TTS: Download complete]")
        except Exception as e:
            print(f"    [Piper TTS: Download failed - {e}]")

    def _init_piper(self):
        """Check piper binary is available and read config."""
        try:
            # Check piper binary exists
            if not PIPER_BIN.exists():
                print(f"    [Piper TTS: Binary not found at {PIPER_BIN}]")
                print(f"    [Download from https://github.com/rhasspy/piper/releases]")
                return

            # Read sample rate from config
            with open(self.config_path) as f:
                config = json.load(f)
                self._sample_rate = config.get("audio", {}).get("sample_rate", 22050)

            self._available = True
            print(f"    [Piper TTS: Ready with voice '{PIPER_MODEL_NAME}']")
        except Exception as e:
            print(f"    [Piper TTS: Failed to init - {e}]")

    def is_available(self) -> bool:
        """Check if TTS is ready to use."""
        return self._available

    def speak(self, text: str, blocking: bool = True):
        """
        Speak the given text.

        Args:
            text: Text to speak
            blocking: If True, wait for speech to complete. If False, speak in background.
        """
        if not self.is_available():
            print(f"  [TTS not available - would say: '{text}']")
            return

        if blocking:
            self._do_speak(text)
        else:
            thread = threading.Thread(target=self._do_speak, args=(text,), daemon=True)
            thread.start()

    def _do_speak(self, text: str):
        """Actually perform the speech synthesis and playback using piper binary."""
        with self._lock:
            if self._speaking:
                return  # Don't overlap speech
            self._speaking = True

        try:
            # Use piper binary to generate raw audio, pipe directly to aplay
            piper_cmd = [
                str(PIPER_BIN),
                "--model", str(self.model_path),
                "--output-raw"
            ]

            aplay_cmd = [
                "aplay",
                "-D", self._audio_device,
                "-r", str(self._sample_rate),
                "-f", "S16_LE",
                "-t", "raw",
                "-c", "1",
                "-q",
                "-"
            ]

            # Pipe: echo text | piper --output-raw | aplay
            piper_proc = subprocess.Popen(
                piper_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._env
            )

            aplay_proc = subprocess.Popen(
                aplay_cmd,
                stdin=piper_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Send text to piper
            piper_proc.stdin.write(text.encode('utf-8'))
            piper_proc.stdin.close()

            # Wait for playback to complete
            aplay_proc.wait(timeout=30)
            piper_proc.wait(timeout=5)

        except subprocess.TimeoutExpired:
            print("  [TTS timeout]")
        except Exception as e:
            print(f"  [TTS error: {e}]")
        finally:
            self._speaking = False

    def speak_async(self, text: str):
        """Speak text in background (non-blocking)."""
        self.speak(text, blocking=False)


def create_tts() -> PiperTTS:
    """Create the Piper TTS engine."""
    tts = PiperTTS()
    if not tts.is_available():
        print("    [WARNING: TTS not available - voice responses disabled]")
    return tts

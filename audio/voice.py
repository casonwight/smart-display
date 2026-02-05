"""Voice control module - wake word detection and command processing."""

import os
# Suppress ONNX Runtime GPU discovery warning (must be before any onnxruntime import)
# Set both os.environ and os.putenv for maximum compatibility
os.environ["ORT_LOG_LEVEL"] = "3"
os.environ["ONNXRUNTIME_LOG_SEVERITY_LEVEL"] = "3"
os.environ["ORT_DISABLE_ALL"] = "1"  # Disable all optional providers
os.putenv("ORT_LOG_LEVEL", "3")
os.putenv("ONNXRUNTIME_LOG_SEVERITY_LEVEL", "3")

import re
import time
import wave
import json
import struct
import threading
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable, Dict, List, Tuple, Any

from config import (
    MODELS_DIR, WAKE_WORD,
    VOICE_MIC_DEVICE, VOICE_SAMPLE_RATE, VOICE_COMMAND_DURATION,
    PORCUPINE_ACCESS_KEY, PORCUPINE_MODEL_PATH
)


# Audio recording settings
SAMPLE_RATE = VOICE_SAMPLE_RATE
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit audio
MIC_DEVICE = VOICE_MIC_DEVICE
COMMAND_RECORD_SECONDS = VOICE_COMMAND_DURATION



@dataclass
class VoiceCommand:
    """Represents a parsed voice command."""
    intent: str
    params: Dict[str, Any]
    raw_text: str


class IntentParser:
    """Parse transcribed text into intents and parameters."""

    # Regex patterns for each intent
    # Order matters - more specific patterns should come first
    PATTERNS: Dict[str, List[Tuple[re.Pattern, Callable]]] = {}

    # Word numbers to digits
    WORD_TO_NUM = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
        "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
        "forty": 40, "fifty": 50, "sixty": 60,
        # Common compound numbers
        "twenty-one": 21, "twenty-two": 22, "twenty-three": 23,
        "twenty-four": 24, "twenty-five": 25, "twenty-six": 26,
        "twenty-seven": 27, "twenty-eight": 28, "twenty-nine": 29,
        "thirty-one": 31, "thirty-two": 32, "thirty-three": 33,
        "thirty-four": 34, "thirty-five": 35, "thirty-six": 36,
        "thirty-seven": 37, "thirty-eight": 38, "thirty-nine": 39,
        "forty-five": 45, "ninety": 90,
    }

    # Noise words to filter from recipe names
    NOISE_WORDS = {"which", "the", "a", "an", "some", "any", "that", "this", "about", "for"}

    # Category aliases
    CATEGORY_ALIASES = {
        "dessert": "Desserts", "desserts": "Desserts", "sweets": "Desserts", "sweet": "Desserts",
        "breakfast": "Breakfast", "brunch": "Breakfast", "morning": "Breakfast",
        "main": "Main Dishes", "mains": "Main Dishes", "main dish": "Main Dishes",
        "main dishes": "Main Dishes", "dinner": "Main Dishes", "lunch": "Main Dishes",
        "entree": "Main Dishes", "entrees": "Main Dishes",
        "side": "Sides", "sides": "Sides", "side dish": "Sides", "side dishes": "Sides",
    }

    # Pattern to match either digits or word numbers
    NUM_PATTERN = r"(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|ninety|twenty-\w+|thirty-\w+|forty-\w+)"

    def __init__(self):
        # Build pattern list with extractors
        self.PATTERNS = {
            # === TIMER COMMANDS ===
            "timer_start": [
                # "set a timer for 5 minutes" / "start timer for thirty seconds"
                (re.compile(rf"(?:start|set|add|create|make)\s+(?:a\s+)?timer\s+(?:for\s+)?{self.NUM_PATTERN}\s*(minutes?|mins?|seconds?|secs?|hours?|hrs?)", re.I),
                 self._extract_timer_duration),
                # "5 minute timer" / "ten minute timer"
                (re.compile(rf"{self.NUM_PATTERN}\s*(minutes?|mins?|seconds?|secs?|hours?|hrs?)\s+timer", re.I),
                 self._extract_timer_duration),
            ],
            "timer_pause": [
                # "pause the timer" / "pause my timer" / "pause the first timer"
                (re.compile(r"pause\s+(?:the\s+|my\s+)?(?:(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+)?timer", re.I),
                 self._extract_timer_ordinal),
            ],
            "timer_resume": [
                # "resume the timer" / "unpause my timer" / "continue the timer"
                (re.compile(r"(?:resume|unpause|continue|start)\s+(?:the\s+|my\s+)?(?:(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+)?timer", re.I),
                 self._extract_timer_ordinal),
            ],
            "timer_stop": [
                # "stop the timer" / "cancel timer" / "delete the timer"
                # With optional ordinal: "cancel the first timer" / "stop my second timer"
                (re.compile(r"(?:stop|cancel|delete|clear)\s+(?:the\s+|my\s+)?(?:(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+)?(?:timer|timers)", re.I),
                 self._extract_timer_ordinal),
            ],
            "timer_stop_all": [
                # "cancel all timers" / "clear all timers"
                (re.compile(r"(?:stop|cancel|delete|clear)\s+all\s+(?:the\s+)?timers?", re.I),
                 lambda m: {}),
            ],
            "timer_status": [
                # "how much time is left" / "timer status" / "check the timer"
                # Also: "how much time is left on my timer" / "how much time has left"
                (re.compile(r"(?:how\s+much\s+time\s+(?:is|has)?\s*(?:left|remaining)(?:\s+on\s+(?:the|my)\s+(?:(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+)?timer)?|timer\s+status|check\s+(?:the|my)\s+(?:(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+)?timer|what.s\s+(?:the|my)\s+timer)", re.I),
                 self._extract_timer_ordinal),
            ],
            "timer_count": [
                # "how many timers" / "how many timers do I have"
                (re.compile(r"how\s+many\s+timers", re.I),
                 lambda m: {}),
            ],
            "timer_add_time": [
                # "add 5 minutes to the timer" / "add 5 minutes to my timer"
                (re.compile(rf"add\s+{self.NUM_PATTERN}\s*(minutes?|mins?|seconds?|secs?)\s+(?:to\s+)?(?:the|my)\s+(?:(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+)?timer", re.I),
                 self._extract_timer_duration_with_ordinal),
            ],

            # === RECIPE COMMANDS ===
            "recipe_show": [
                # "show me the chocolate chip cookie recipe" / "go to the cookies recipe"
                (re.compile(r"(?:show|find|open|display|pull up|get|go\s+to)\s+(?:me\s+)?(?:the\s+)?(.+?)\s+recipe", re.I),
                 self._extract_recipe_name),
                # "show recipe for chocolate chip cookies"
                (re.compile(r"(?:show|find|open|get|go\s+to)\s+(?:the\s+)?recipe\s+(?:for|about)\s+(.+)", re.I),
                 self._extract_recipe_name),
                # "how do I make pancakes" / "how to make cookies"
                (re.compile(r"how\s+(?:do\s+(?:I|you)\s+)?(?:make|cook|bake|prepare)\s+(.+)", re.I),
                 self._extract_recipe_name),
            ],
            "recipe_ingredients": [
                # "what's in the pancakes recipe" / "what ingredients for cookies"
                (re.compile(r"what(?:'s| is| are)\s+(?:in\s+)?(?:the\s+)?(.+?)\s+recipe", re.I),
                 self._extract_recipe_name),
                (re.compile(r"(?:what\s+)?ingredients\s+(?:for|in)\s+(?:the\s+)?(.+)", re.I),
                 self._extract_recipe_name),
            ],
            "recipe_cook_time": [
                # Total time to make the recipe (from metadata)
                # Context-aware: "how long does it take" / "how long to make"
                (re.compile(r"how\s+long\s+(?:does\s+(?:it|this)\s+take|will\s+(?:it|this)\s+take|to\s+make(?:\s+(?:these|this|them|it))?)", re.I),
                 lambda m: {"use_current": True}),
                # "how long to make cookies" / "how long does it take to make cookies"
                (re.compile(r"how\s+long\s+(?:does\s+it\s+take\s+)?(?:to\s+)?(?:make|prepare)\s+(?:the\s+)?(.+)", re.I),
                 self._extract_recipe_name),
                # "how long will cookies take"
                (re.compile(r"how\s+long\s+(?:will|do|does)\s+(?:the\s+)?(.+?)\s+take", re.I),
                 self._extract_recipe_name),
            ],
            "recipe_oven_time": [
                # How long in the oven (parsed from instructions)
                # Context-aware: "how long in the oven" / "how long do I bake these"
                (re.compile(r"how\s+long\s+(?:in\s+the\s+oven|do\s+(?:I|they|the|these|this)\s+(?:bake|cook|roast)|should\s+(?:I|they|it)\s+(?:bake|cook|roast)|(?:do|does|will|are|should)\s+(?:the\s+)?(?:these|this|them|it|they)\s+(?:bake|cook|go\s+in|be\s+in|stay\s+in))", re.I),
                 lambda m: {"use_current": True}),
                # "how long do cookies bake" / "how long are cookies in the oven"
                (re.compile(r"how\s+long\s+(?:do|does|will|are|should)\s+(?:the\s+)?(.+?)\s+(?:bake|cook|roast|go\s+in|be\s+in|stay\s+in)\s*(?:the\s+)?(?:oven|for)?", re.I),
                 self._extract_recipe_name),
            ],
            "recipe_temperature": [
                # "what temperature for cookies" / "what temp do I set the oven"
                (re.compile(r"what\s+(?:temperature|temp)\s+(?:for|do\s+I\s+(?:need|set)|should\s+I\s+set)\s+(?:the\s+)?(?:oven\s+)?(?:for\s+)?(?:the\s+)?(.+)", re.I),
                 self._extract_recipe_name),
                (re.compile(r"what\s+(?:temperature|temp)\s+(?:do\s+I\s+(?:need|set)|should\s+I\s+set)\s+(?:the\s+)?oven", re.I),
                 lambda m: {"use_current": True}),
                # Context-aware: "what temperature" (uses current recipe)
                (re.compile(r"what(?:'s|\s+is)?\s+(?:the\s+)?(?:oven\s+)?(?:temperature|temp)", re.I),
                 lambda m: {"use_current": True}),
            ],
            "category_browse": [
                # "show me desserts" / "what desserts do we have"
                (re.compile(r"(?:show|list|display)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(\w+(?:\s+\w+)?)", re.I),
                 self._extract_category),
                (re.compile(r"what\s+(\w+(?:\s+\w+)?)\s+(?:do\s+we\s+have|are\s+there|recipes?)", re.I),
                 self._extract_category),
                (re.compile(r"what(?:'s| is)\s+for\s+(dinner|breakfast|lunch|dessert)", re.I),
                 self._extract_category),
            ],

            # === NAVIGATION COMMANDS ===
            "go_home": [
                # "go home" / "go to home" / "home screen"
                (re.compile(r"(?:go\s+)?(?:to\s+)?home(?:\s+screen)?", re.I),
                 lambda m: {}),
            ],
            "go_back": [
                # "go back" / "back"
                (re.compile(r"(?:go\s+)?back", re.I),
                 lambda m: {}),
            ],
            "open_menu": [
                # "open menu" / "show menu" / "main menu"
                (re.compile(r"(?:open|show|go\s+to)\s+(?:the\s+)?(?:main\s+)?menu", re.I),
                 lambda m: {}),
            ],
            "open_timers": [
                # "open timers" / "go to timers" / "show me my timer"
                (re.compile(r"(?:open|show|go\s+to)\s+(?:the\s+|my\s+|me\s+(?:the\s+|my\s+)?)?timers?", re.I),
                 lambda m: {}),
            ],
            "open_recipes": [
                # "open recipes" / "go to recipes"
                (re.compile(r"(?:open|show|go\s+to)\s+(?:the\s+)?recipes?", re.I),
                 lambda m: {}),
            ],
            "open_music": [
                # "open music" / "go to music"
                (re.compile(r"(?:open|show|go\s+to)\s+(?:the\s+)?music", re.I),
                 lambda m: {}),
            ],

            # === WEATHER COMMANDS ===
            "weather": [
                # "what's the weather" / "how's the weather"
                (re.compile(r"(?:what(?:'s| is)|how(?:'s| is))\s+(?:the\s+)?weather", re.I),
                 lambda m: {"type": "full"}),
                # "what's the weather today/outside"
                (re.compile(r"(?:what(?:'s| is)|how(?:'s| is))\s+(?:the\s+)?weather\s+(?:like\s+)?(?:today|outside|out)", re.I),
                 lambda m: {"type": "full"}),
                # "is it going to rain" / "will it rain"
                (re.compile(r"(?:is\s+it\s+going\s+to|will\s+it)\s+rain", re.I),
                 lambda m: {"type": "rain"}),
            ],
            "temperature": [
                # "what's the temperature" / "how hot is it"
                (re.compile(r"(?:what(?:'s| is))\s+(?:the\s+)?(?:temperature|temp)", re.I),
                 lambda m: {}),
                (re.compile(r"how\s+(?:hot|cold|warm)\s+is\s+it", re.I),
                 lambda m: {}),
                (re.compile(r"(?:what(?:'s| is))\s+(?:the\s+)?(?:temperature|temp)\s+(?:outside|out)", re.I),
                 lambda m: {}),
            ],

            # === TIME COMMANDS ===
            "time": [
                # "what time is it" / "what's the time"
                (re.compile(r"what(?:\s+time\s+is\s+it|'s\s+the\s+time)", re.I),
                 lambda m: {}),
            ],
            "date": [
                # "what's the date" / "what day is it" / "what's the day" / "what day is today"
                (re.compile(r"what(?:'s|\s+is)\s+(?:the\s+)?(?:date|today|day)", re.I),
                 lambda m: {}),
                (re.compile(r"what\s+day\s+is\s+(?:it|today)", re.I),
                 lambda m: {}),
            ],

            # === SPOTIFY COMMANDS (paused but keep patterns) ===
            "spotify_play": [
                # "play shape of you" / "play taylor swift"
                (re.compile(r"play\s+(.+?)(?:\s+on\s+spotify)?$", re.I),
                 lambda m: {"query": m.group(1).strip()}),
            ],
            "spotify_pause": [
                # "pause the music" / "stop the music" / "pause"
                (re.compile(r"(?:pause|stop)\s+(?:the\s+)?(?:music|song|track|playback)", re.I),
                 lambda m: {}),
                (re.compile(r"^pause$", re.I),
                 lambda m: {}),
            ],
            "spotify_skip": [
                # "skip" / "next track" / "next song" / "skip this"
                (re.compile(r"(?:skip|next)\s*(?:this\s+)?(?:track|song)?", re.I),
                 lambda m: {}),
                (re.compile(r"^(?:skip|next)$", re.I),
                 lambda m: {}),
            ],
        }

    def _parse_number(self, text: str) -> int:
        """Convert a number string (digit or word) to integer."""
        text = text.lower().strip()
        # Try direct digit conversion first
        if text.isdigit():
            return int(text)
        # Try word lookup
        if text in self.WORD_TO_NUM:
            return self.WORD_TO_NUM[text]
        # Try compound like "twenty five" (space instead of hyphen)
        text_hyphen = text.replace(" ", "-")
        if text_hyphen in self.WORD_TO_NUM:
            return self.WORD_TO_NUM[text_hyphen]
        # Default
        return 5  # Fallback to 5 minutes if unparseable

    def _clean_recipe_name(self, name: str) -> str:
        """Clean up a recipe name - remove possessives, noise words, punctuation."""
        # Remove possessives ('s, s')
        name = re.sub(r"'s\b", "", name)
        name = re.sub(r"s'\b", "s", name)
        # Remove trailing punctuation
        name = name.rstrip(".,!?")
        # Remove noise words from the beginning
        words = name.split()
        while words and words[0].lower() in self.NOISE_WORDS:
            words.pop(0)
        # Remove noise words from the end
        while words and words[-1].lower() in self.NOISE_WORDS:
            words.pop()
        return " ".join(words).strip()

    def _extract_recipe_name(self, match: re.Match) -> Dict[str, Any]:
        """Extract and clean recipe name from match."""
        raw_name = match.group(1).strip()
        clean_name = self._clean_recipe_name(raw_name)
        return {"name": clean_name, "raw_name": raw_name}

    def _extract_category(self, match: re.Match) -> Dict[str, Any]:
        """Extract category from match."""
        raw_cat = match.group(1).strip().lower()
        # Look up category alias
        category = self.CATEGORY_ALIASES.get(raw_cat)
        if category:
            return {"category": category, "raw": raw_cat}
        return {"category": None, "raw": raw_cat}

    # Ordinal word to index (0-based)
    ORDINAL_TO_INDEX = {
        "first": 0, "1st": 0,
        "second": 1, "2nd": 1,
        "third": 2, "3rd": 2,
        "fourth": 3, "4th": 3,
        "fifth": 4, "5th": 4,
    }

    def _parse_ordinal(self, ordinal: Optional[str]) -> Optional[int]:
        """Convert ordinal string to 0-based index."""
        if not ordinal:
            return None
        return self.ORDINAL_TO_INDEX.get(ordinal.lower())

    def _extract_timer_ordinal(self, match: re.Match) -> Dict[str, Any]:
        """Extract optional timer ordinal from command."""
        # Try to find an ordinal in any capture group
        ordinal = None
        for group in match.groups():
            if group and group.lower() in self.ORDINAL_TO_INDEX:
                ordinal = group
                break
        index = self._parse_ordinal(ordinal)
        return {"timer_index": index} if index is not None else {}

    def _extract_timer_duration(self, match: re.Match) -> Dict[str, Any]:
        """Extract duration from timer command."""
        amount = self._parse_number(match.group(1))
        unit = match.group(2).lower()

        # Normalize to minutes
        if unit.startswith("sec"):
            # Keep seconds for actual timer, but also provide minutes
            minutes = max(1, amount // 60) if amount >= 60 else 1
            seconds = amount
        elif unit.startswith("hour") or unit.startswith("hr"):
            minutes = amount * 60
            seconds = minutes * 60
        else:
            # Already minutes
            minutes = amount
            seconds = minutes * 60

        return {"minutes": minutes, "seconds": seconds, "raw_amount": amount, "raw_unit": unit}

    def _extract_timer_duration_with_ordinal(self, match: re.Match) -> Dict[str, Any]:
        """Extract duration and optional ordinal from add-time command."""
        result = self._extract_timer_duration(match)
        # Check for ordinal in group 3
        if len(match.groups()) >= 3 and match.group(3):
            index = self._parse_ordinal(match.group(3))
            if index is not None:
                result["timer_index"] = index
        return result

    def parse(self, text: str) -> Optional[VoiceCommand]:
        """Parse text into a VoiceCommand if it matches any pattern."""
        if not text:
            return None

        text = text.strip()

        for intent, patterns in self.PATTERNS.items():
            for pattern, extractor in patterns:
                match = pattern.search(text)
                if match:
                    params = extractor(match)
                    return VoiceCommand(intent=intent, params=params, raw_text=text)

        return None


class VoiceController:
    """Orchestrates wake word detection and command processing."""

    def __init__(self, on_command_callback: Callable[[str, Dict[str, Any]], None], audio_player=None):
        """
        Initialize the voice controller.

        Args:
            on_command_callback: Called when a command is recognized.
                                 Receives (intent: str, params: dict)
            audio_player: Optional AudioPlayer instance for feedback beeps
        """
        self.on_command = on_command_callback
        self.audio = audio_player
        self.intent_parser = IntentParser()

        self._running = False
        self._listen_thread: Optional[threading.Thread] = None
        self._porcupine = None
        self._whisper_model = None

        # State tracking
        self._listening_for_command = False
        self._last_wake_time = 0

        # Paths
        self._models_dir = Path(MODELS_DIR)
        self._models_dir.mkdir(parents=True, exist_ok=True)

        # Recording buffer file
        self._recording_path = Path("/tmp/voice_command.wav")

    def start(self):
        """Start the voice controller background thread."""
        if self._running:
            return

        # Load models (this may download them on first run)
        if not self._load_models():
            print("    [Voice control disabled - models failed to load]")
            return

        self._running = True
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()
        print("    [Voice control active - say 'Hey Olly' to activate]")

    def stop(self):
        """Stop the voice controller."""
        self._running = False
        if self._listen_thread:
            self._listen_thread.join(timeout=2)
            self._listen_thread = None
        if self._porcupine:
            self._porcupine.delete()
            self._porcupine = None

    def _load_models(self) -> bool:
        """Load Porcupine and Whisper models."""
        # Load Porcupine for wake word detection
        try:
            import pvporcupine

            porcupine_path = Path(PORCUPINE_MODEL_PATH)
            if not porcupine_path.exists():
                print(f"    [Porcupine model not found at {porcupine_path}]")
                return False

            self._porcupine = pvporcupine.create(
                access_key=PORCUPINE_ACCESS_KEY,
                keyword_paths=[str(porcupine_path)]
            )
            print(f"    [Porcupine wake word loaded]")
        except ImportError:
            print("    [pvporcupine not installed - run: pip install pvporcupine]")
            return False
        except Exception as e:
            print(f"    [Failed to load Porcupine: {e}]")
            return False

        # Load Whisper model for command transcription
        try:
            from faster_whisper import WhisperModel

            # Use tiny.en for faster transcription - accurate enough for short commands
            # Model downloads on first use, then runs locally from cache
            print(f"    [Loading Whisper model...]")
            # Redirect stderr to suppress ONNX GPU warning during model init
            import sys
            import io
            old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            try:
                self._whisper_model = WhisperModel(
                    "tiny.en",
                    device="cpu",
                    compute_type="int8",
                    local_files_only=False,  # Allow download first time, then cached
                    cpu_threads=4,  # Limit CPU threads
                )
                # Warmup: trigger lazy loading by doing a transcription
                import wave
                import struct
                warmup_path = "/dev/shm/whisper_warmup.wav"
                with wave.open(warmup_path, 'wb') as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(struct.pack('<' + 'h' * 1600, *([0] * 1600)))  # 0.1s silence
                # Consume the generator to actually run the transcription
                segments, _ = self._whisper_model.transcribe(warmup_path, language="en")
                for _ in segments:
                    pass
                os.remove(warmup_path)
            finally:
                sys.stderr = old_stderr
            print(f"    [Whisper model loaded]")
        except ImportError:
            print("    [faster-whisper not installed - run: pip install faster-whisper]")
            return False
        except Exception as e:
            print(f"    [Failed to load Whisper model: {e}]")
            return False

        return True

    def _listen_loop(self):
        """Main listening loop using Porcupine for wake word detection."""
        print("  [Voice: Listening for 'Hey Ollie'...]")

        frame_length = self._porcupine.frame_length  # Usually 512 samples

        while self._running:
            try:
                # Start continuous recording with arecord
                process = subprocess.Popen(
                    [
                        "arecord",
                        "-D", MIC_DEVICE,
                        "-f", "S16_LE",
                        "-r", str(SAMPLE_RATE),
                        "-c", str(CHANNELS),
                        "-t", "raw",
                        "-q",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )

                while self._running:
                    # Read one frame of audio
                    audio_bytes = process.stdout.read(frame_length * 2)  # 2 bytes per sample
                    if not audio_bytes or len(audio_bytes) < frame_length * 2:
                        break

                    # Convert to int16 array
                    audio_frame = struct.unpack(f"{frame_length}h", audio_bytes)

                    # Check for wake word
                    keyword_index = self._porcupine.process(audio_frame)
                    if keyword_index >= 0:
                        print(f"  [Wake word detected!]")
                        # Stop the recording process before handling wake word
                        process.terminate()
                        process.wait()
                        self._on_wake_word_detected()
                        break

                # Clean up process if still running
                if process.poll() is None:
                    process.terminate()
                    process.wait()

            except Exception as e:
                print(f"  [Voice listen error: {e}]")
                time.sleep(1)

    def _on_wake_word_detected(self):
        """Handle wake word detection - play beep and listen for command."""
        # Debounce - don't trigger if we just triggered
        now = time.time()
        if now - self._last_wake_time < 2:
            return
        self._last_wake_time = now

        # Play confirmation beep
        if self.audio:
            self.audio.beep(frequency=880, duration_ms=150, volume=0.6)

        # Record command audio
        print(f"  [Listening for command...]")
        audio_data = self._record_audio(duration=COMMAND_RECORD_SECONDS, for_wake_word=False)

        if not audio_data:
            print(f"  [Failed to record command]")
            self._play_error_tone()
            return

        # Save to file for Whisper
        self._save_wav(audio_data, self._recording_path)

        # Transcribe with Whisper
        text = self._transcribe_audio(self._recording_path)
        if not text:
            print(f"  [Transcription failed or empty]")
            self._play_error_tone()
            return

        print(f"  [Transcribed: '{text}']")

        # Parse intent
        command = self.intent_parser.parse(text)
        if command:
            print(f"  [Intent: {command.intent}, Params: {command.params}]")
            self._play_success_tone()
            # Call the callback
            if self.on_command:
                self.on_command(command.intent, command.params)
        else:
            print(f"  [No matching intent for: '{text}']")
            self._play_error_tone()

    def _record_audio(self, duration: float, for_wake_word: bool = False) -> Optional[bytes]:
        """Record audio from the microphone using arecord."""
        try:
            # Use arecord to capture audio
            cmd = [
                "arecord",
                "-D", MIC_DEVICE,
                "-f", "S16_LE",
                "-r", str(SAMPLE_RATE),
                "-c", str(CHANNELS),
                "-t", "raw",
                "-d", str(int(duration)),
                "-q",  # Quiet
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=duration + 2
            )

            if result.returncode != 0:
                if not for_wake_word:  # Don't spam errors for wake word detection
                    print(f"  [arecord error: {result.stderr.decode()}]")
                return None

            return result.stdout

        except subprocess.TimeoutExpired:
            return None
        except Exception as e:
            if not for_wake_word:
                print(f"  [Recording error: {e}]")
            return None

    def _save_wav(self, audio_data: bytes, path: Path):
        """Save raw audio data to a WAV file."""
        with wave.open(str(path), 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data)

    def _transcribe_audio(self, audio_path: Path) -> Optional[str]:
        """Transcribe audio file using Whisper."""
        if not self._whisper_model:
            return None

        try:
            segments, info = self._whisper_model.transcribe(
                str(audio_path),
                language="en",
                beam_size=1,  # Faster than beam_size=5, still accurate for short commands
                vad_filter=True,  # Filter out silence
                without_timestamps=True,  # Don't need timestamps, saves time
            )

            # Combine all segments
            text = " ".join(seg.text for seg in segments).strip()
            return text if text else None

        except Exception as e:
            print(f"  [Transcription error: {e}]")
            return None

    def _play_success_tone(self):
        """Play a confirmation tone for successful command."""
        if self.audio:
            # Two quick ascending beeps
            self.audio.beep(frequency=880, duration_ms=100, volume=0.5)
            time.sleep(0.05)
            self.audio.beep(frequency=1100, duration_ms=100, volume=0.5)

    def _play_error_tone(self):
        """Play an error tone for unrecognized command."""
        if self.audio:
            # Low descending tone
            self.audio.beep(frequency=440, duration_ms=200, volume=0.4)

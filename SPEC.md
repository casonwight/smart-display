# Smart Display - Technical Specification

## Overview

A Raspberry Pi 5-based smart display with a 7.5" e-ink screen (800x480), dual speakers, USB microphone, rotary encoder, and two buttons. All processing runs locally.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                              │
│                    (Event Loop & State)                     │
└─────────────────────────────────────────────────────────────┘
         │              │              │              │
         ▼              ▼              ▼              ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│   display/  │ │   input/    │ │   audio/    │ │   apps/     │
│             │ │             │ │             │ │             │
│ renderer.py │ │ encoder.py  │ │ player.py   │ │ home.py     │
│ regions.py  │ │ buttons.py  │ │ voice.py    │ │ recipes.py  │
│ assets.py   │ │             │ │ spotify.py  │ │ timers.py   │
│             │ │             │ │             │ │ music.py    │
└─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

---

## Display System

### Hardware Constraints
- **Resolution**: 800x480 pixels, 1-bit (black/white) or 4-grayscale
- **Full refresh**: ~2-3 seconds, causes visible flash
- **Partial refresh**: ~0.3-0.5 seconds, no flash, but ghosting accumulates
- **Recommendation**: Full refresh every 10-20 partial refreshes to clear ghosting

### Region-Based Rendering

The display is divided into **regions** that can be independently refreshed:

```
┌────────────────────────────────────────────────────────────┐
│                     HEADER (800x60)                        │
│                   [Time] [Status Icons]                    │
├────────────────────────────────────────────────────────────┤
│                                                            │
│                                                            │
│                    CONTENT (800x360)                       │
│                                                            │
│                   [App-specific content]                   │
│                                                            │
│                                                            │
├────────────────────────────────────────────────────────────┤
│                     FOOTER (800x60)                        │
│              [Navigation] [Volume] [Controls]              │
└────────────────────────────────────────────────────────────┘
```

### Region Class

```python
@dataclass
class Region:
    name: str
    x: int
    y: int
    width: int
    height: int
    dirty: bool = False
    last_content_hash: str = ""

# Predefined regions
REGIONS = {
    "header": Region("header", 0, 0, 800, 60),
    "content": Region("content", 0, 60, 800, 360),
    "footer": Region("footer", 0, 420, 800, 60),
    # Sub-regions for menu items (dynamically created)
}
```

### Renderer Functions

```python
class DisplayRenderer:
    def __init__(self, epd):
        self.epd = epd
        self.framebuffer = Image.new('1', (800, 480), 255)  # White background
        self.regions: dict[str, Region] = {}
        self.partial_refresh_count = 0
        self.MAX_PARTIAL_BEFORE_FULL = 15

    def update_region(self, region_name: str, image: Image) -> None:
        """Update a specific region's content in the framebuffer."""

    def render_dirty_regions(self) -> None:
        """Partial refresh only regions marked dirty."""

    def force_full_refresh(self) -> None:
        """Full screen refresh to clear ghosting."""

    def should_full_refresh(self) -> bool:
        """Check if we've hit the partial refresh limit."""
```

### Content Hashing for Smart Updates

Only refresh a region if content actually changed:

```python
def content_changed(self, region_name: str, new_image: Image) -> bool:
    """Compare hash of new content vs cached hash."""
    new_hash = hashlib.md5(new_image.tobytes()).hexdigest()
    if self.regions[region_name].last_content_hash != new_hash:
        self.regions[region_name].last_content_hash = new_hash
        return True
    return False
```

---

## Input System

### Rotary Encoder

```python
class RotaryEncoder:
    def __init__(self, pin_a=5, pin_b=6, pin_sw=13):
        self.encoder = gpiozero.RotaryEncoder(pin_a, pin_b)
        self.button = gpiozero.Button(pin_sw, pull_up=True)

        self.on_rotate_cw: Callable = None   # Clockwise callback
        self.on_rotate_ccw: Callable = None  # Counter-clockwise callback
        self.on_press: Callable = None       # Press callback
        self.on_long_press: Callable = None  # Long press (>1s) callback

    def get_steps(self) -> int:
        """Return rotation steps since last check (+ = CW, - = CCW)."""
```

### Volume Buttons

```python
class VolumeButtons:
    def __init__(self, pin_up=26, pin_down=16):
        self.btn_up = gpiozero.Button(pin_up, pull_up=True)
        self.btn_down = gpiozero.Button(pin_down, pull_up=True)

    def on_volume_up(self, callback: Callable) -> None:
        """Register volume up callback."""

    def on_volume_down(self, callback: Callable) -> None:
        """Register volume down callback."""
```

---

## Audio System

### Audio Player

```python
class AudioPlayer:
    def __init__(self, device="hw:0,0"):
        self.device = device
        self.volume = 50  # 0-100

    def set_volume(self, level: int) -> None:
        """Set system volume (0-100)."""

    def play_sound(self, path: str) -> None:
        """Play a sound file (for UI feedback, timer alarms)."""

    def beep(self, frequency=440, duration_ms=100) -> None:
        """Play a simple beep for UI feedback."""
```

### Spotify Integration (via Spotify Connect)

The e-ink display is too slow for real-time playback UI. Strategy:

1. **Use Raspotify** - Makes Pi a Spotify Connect device
2. **Control via Spotify Web API** - Play/pause/skip from the display
3. **Minimal UI updates** - Only refresh when track changes, not progress bar

```python
class SpotifyController:
    def __init__(self):
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            redirect_uri="http://localhost:8888/callback",
            scope="user-read-playback-state,user-modify-playback-state"
        ))
        self.current_track: dict = None
        self.is_playing: bool = False

    def get_current_track(self) -> dict:
        """Fetch currently playing track info."""

    def play(self) -> None:
        """Resume playback."""

    def pause(self) -> None:
        """Pause playback."""

    def next_track(self) -> None:
        """Skip to next track."""

    def previous_track(self) -> None:
        """Go to previous track."""

    def search_and_play(self, query: str) -> None:
        """Search for a track/album/playlist and start playing."""
```

### Spotify UI for E-Ink

```
┌────────────────────────────────────────────────────────────┐
│ 🎵 Spotify                                    ▶ Playing    │
├────────────────────────────────────────────────────────────┤
│                                                            │
│     ┌──────────────┐                                       │
│     │              │   Surface Pressure                    │
│     │  Album Art   │   Jessica Darrow                      │
│     │  (dithered)  │                                       │
│     │              │   Encanto (Soundtrack)                │
│     └──────────────┘                                       │
│                                                            │
│                         advancement unknown                 │
│                                                            │
├────────────────────────────────────────────────────────────┤
│        ⏮  Previous    ⏯  Play/Pause    ⏭  Next           │
└────────────────────────────────────────────────────────────┘
```

**Key decisions:**
- No progress bar (requires constant updates)
- Show "advancement unknown" or poll every 30s for rough position
- Dither album art to 1-bit for display
- Only refresh content region when track changes

---

## Voice Control System (Local)

### Options for Local Speech-to-Text

| Engine | Size | Speed | Accuracy | Offline |
|--------|------|-------|----------|---------|
| **Vosk** | 50MB-1.8GB | Fast | Good | Yes |
| **Whisper.cpp** | 75MB-1.5GB | Medium | Excellent | Yes |
| **Pocketsphinx** | 30MB | Very Fast | Fair | Yes |

**Recommendation**: **Vosk** with small model for wake word, **Whisper.cpp** (small/base) for command recognition.

### Wake Word Detection

```python
class WakeWordDetector:
    def __init__(self, wake_word="hey olly"):
        self.vosk_model = vosk.Model("models/vosk-small")
        self.wake_word = wake_word.lower()
        self.listening = False

    def start_listening(self) -> None:
        """Start background wake word detection."""

    def on_wake_word(self, callback: Callable) -> None:
        """Register callback for when wake word is detected."""
```

### Command Recognition

```python
class VoiceCommander:
    def __init__(self):
        self.whisper = WhisperModel("base.en")  # or small.en

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file to text."""

    def parse_command(self, text: str) -> dict:
        """Parse transcribed text into structured command."""
        # Returns: {"intent": "timer", "action": "start", "duration": 720}
```

### Intent Parsing (Rule-Based, No LLM Needed)

```python
INTENT_PATTERNS = {
    "timer_start": [
        r"(?:start|set|create) (?:a )?timer (?:for )?(\d+)\s*(minutes?|seconds?|hours?)",
        r"(\d+)\s*(minute|second|hour) timer",
    ],
    "timer_stop": [
        r"(?:stop|cancel|delete) (?:the )?timer",
    ],
    "recipe_show": [
        r"(?:show|find|get|open) (?:me )?(?:the )?(.+?) recipe",
        r"recipe (?:for )?(.+)",
    ],
    "spotify_play": [
        r"play (.+?)(?: on spotify)?$",
    ],
    "spotify_pause": [
        r"(?:pause|stop) (?:the )?music",
    ],
    "spotify_skip": [
        r"(?:skip|next)(?: track| song)?",
    ],
}

def parse_intent(text: str) -> dict:
    """Match text against intent patterns."""
    text = text.lower().strip()
    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return {"intent": intent, "groups": match.groups()}
    return {"intent": "unknown", "text": text}
```

---

## Application Screens

### State Machine

```python
class AppState(Enum):
    HOME = "home"
    MENU = "menu"
    RECIPES = "recipes"
    RECIPE_CATEGORIES = "recipe_categories"
    RECIPE_LIST = "recipe_list"
    RECIPE_VIEW = "recipe_view"
    TIMERS = "timers"
    TIMER_NEW = "timer_new"
    MUSIC = "music"

class StateMachine:
    def __init__(self):
        self.state = AppState.HOME
        self.history: list[AppState] = []

    def transition(self, new_state: AppState) -> None:
        """Transition to new state, pushing current to history."""

    def back(self) -> None:
        """Return to previous state."""
```

### Home Screen

```python
class HomeApp:
    def __init__(self, renderer: DisplayRenderer):
        self.renderer = renderer
        self.otter_image = Image.open("assets/otter.png")

    def render(self) -> None:
        """Render home screen with time and otter."""

    def update_time(self) -> None:
        """Update just the time region (partial refresh)."""
```

**Layout:**
```
┌────────────────────────────────────────────────────────────┐
│                        12:34 PM                            │
├────────────────────────────────────────────────────────────┤
│                                                            │
│                     ┌──────────────┐                       │
│                     │              │                       │
│                     │    Otter     │                       │
│                     │    Image     │                       │
│                     │              │                       │
│                     └──────────────┘                       │
│                                                            │
├────────────────────────────────────────────────────────────┤
│                  Turn dial to open menu                    │
└────────────────────────────────────────────────────────────┘
```

### Menu Screen (App Selector)

```
┌────────────────────────────────────────────────────────────┐
│                         Menu                               │
├────────────────────────────────────────────────────────────┤
│                                                            │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│    │   Recipes    │  │    Timers    │  │    Music     │   │
│    │     [1]      │  │     [2]      │  │     [3]      │   │
│    │  ○ selected  │  │              │  │              │   │
│    └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                            │
│                                                            │
├────────────────────────────────────────────────────────────┤
│           Turn: Navigate    Push: Select    Long: Back     │
└────────────────────────────────────────────────────────────┘
```

Each menu item is its own **region** for partial refresh.

### Recipe App

```python
class RecipeApp:
    def __init__(self, renderer: DisplayRenderer, recipe_dir: str):
        self.renderer = renderer
        self.recipe_dir = recipe_dir
        self.categories: list[str] = []
        self.current_category: str = None
        self.current_recipe: CookLangRecipe = None
        self.scroll_position: int = 0

    def load_categories(self) -> list[str]:
        """Scan recipe directory for category folders."""

    def load_recipes(self, category: str) -> list[str]:
        """Load recipe names from a category folder."""

    def load_recipe(self, category: str, name: str) -> CookLangRecipe:
        """Parse a .cook file into structured recipe."""

    def render_categories(self, selected_idx: int) -> None:
        """Render category selection screen."""

    def render_recipe_list(self, selected_idx: int) -> None:
        """Render recipe list for current category."""

    def render_recipe(self) -> None:
        """Render current recipe with scroll support."""

    def scroll(self, direction: int) -> None:
        """Scroll recipe content up/down."""
```

**CookLang Parser:**
```python
def parse_cooklang(content: str) -> dict:
    """
    Parse CookLang format:
    - @ingredient{amount%unit} for ingredients
    - #cookware{} for cookware
    - ~timer{time%unit} for timers
    """
    # Returns structured recipe dict
```

### Timer App

```python
class TimerApp:
    def __init__(self, renderer: DisplayRenderer, audio: AudioPlayer):
        self.renderer = renderer
        self.audio = audio
        self.timers: list[Timer] = []

    def add_timer(self, seconds: int, label: str = "") -> None:
        """Create a new timer."""

    def delete_timer(self, index: int) -> None:
        """Remove a timer."""

    def delete_all(self) -> None:
        """Clear all timers."""

    def tick(self) -> None:
        """Called every second to update timers."""

    def on_timer_complete(self, timer: Timer) -> None:
        """Handle timer completion (sound alarm, notify)."""

    def render(self, selected_idx: int) -> None:
        """Render timer list."""

@dataclass
class Timer:
    id: str
    label: str
    total_seconds: int
    remaining_seconds: int
    created_at: datetime
```

**Timer Screen:**
```
┌────────────────────────────────────────────────────────────┐
│                        Timers                              │
├────────────────────────────────────────────────────────────┤
│                                                            │
│    ┌────────────────────────────────────────────────────┐  │
│    │ ● Cookies                              05:32       │  │
│    └────────────────────────────────────────────────────┘  │
│    ┌────────────────────────────────────────────────────┐  │
│    │   Pasta                                12:00       │  │
│    └────────────────────────────────────────────────────┘  │
│                                                            │
│    [ + New Timer ]                                         │
│    [ Delete All ]                                          │
│                                                            │
├────────────────────────────────────────────────────────────┤
│           Turn: Navigate    Push: Select    Long: Back     │
└────────────────────────────────────────────────────────────┘
```

### Music App (Spotify)

```python
class MusicApp:
    def __init__(self, renderer: DisplayRenderer, spotify: SpotifyController):
        self.renderer = renderer
        self.spotify = spotify
        self.last_track_id: str = None

    def render(self) -> None:
        """Render Spotify playback screen."""

    def check_track_change(self) -> bool:
        """Poll Spotify API, return True if track changed."""

    def handle_input(self, action: str) -> None:
        """Handle play/pause/next/prev from encoder."""
```

---

## File Structure

```
smart-display/
├── main.py                 # Entry point, event loop
├── config.py               # Configuration constants
├── requirements.txt
│
├── display/
│   ├── __init__.py
│   ├── renderer.py         # DisplayRenderer class
│   ├── regions.py          # Region definitions
│   └── assets.py           # Image loading, dithering
│
├── input/
│   ├── __init__.py
│   ├── encoder.py          # RotaryEncoder class
│   └── buttons.py          # VolumeButtons class
│
├── audio/
│   ├── __init__.py
│   ├── player.py           # AudioPlayer class
│   ├── spotify.py          # SpotifyController class
│   └── voice.py            # WakeWordDetector, VoiceCommander
│
├── apps/
│   ├── __init__.py
│   ├── home.py             # HomeApp
│   ├── menu.py             # MenuApp
│   ├── recipes.py          # RecipeApp + CookLang parser
│   ├── timers.py           # TimerApp
│   └── music.py            # MusicApp
│
├── assets/
│   ├── otter.png
│   ├── fonts/
│   └── icons/
│
├── recipes/                # CookLang recipe files
│   ├── desserts/
│   │   └── chocolate_chip_cookies.cook
│   ├── main_dishes/
│   └── breakfast/
│
└── models/                 # Voice recognition models
    ├── vosk-small/
    └── whisper-base/
```

---

## Main Event Loop

```python
async def main():
    # Initialize hardware
    epd = epd7in5_V2.EPD()
    epd.init()

    renderer = DisplayRenderer(epd)
    encoder = RotaryEncoder()
    buttons = VolumeButtons()
    audio = AudioPlayer()
    spotify = SpotifyController()
    voice = VoiceCommander()

    # Initialize apps
    apps = {
        AppState.HOME: HomeApp(renderer),
        AppState.RECIPES: RecipeApp(renderer, "recipes/"),
        AppState.TIMERS: TimerApp(renderer, audio),
        AppState.MUSIC: MusicApp(renderer, spotify),
    }

    state = StateMachine()

    # Register input callbacks
    encoder.on_rotate_cw = lambda: handle_rotate(state, apps, 1)
    encoder.on_rotate_ccw = lambda: handle_rotate(state, apps, -1)
    encoder.on_press = lambda: handle_press(state, apps)
    encoder.on_long_press = lambda: state.back()

    buttons.on_volume_up(lambda: audio.set_volume(audio.volume + 5))
    buttons.on_volume_down(lambda: audio.set_volume(audio.volume - 5))

    # Start voice detection (background thread)
    voice.start_listening()
    voice.on_wake_word(lambda: handle_voice_command(voice, state, apps))

    # Main loop
    while True:
        # Update current app
        apps[state.state].update()

        # Render dirty regions
        renderer.render_dirty_regions()

        # Check for full refresh need
        if renderer.should_full_refresh():
            renderer.force_full_refresh()

        await asyncio.sleep(0.05)  # 20 FPS check rate
```

---

## Dependencies

```
# requirements.txt
Pillow>=10.0.0
gpiozero>=2.0
spidev>=3.6
spotipy>=2.23.0
vosk>=0.3.45
faster-whisper>=0.10.0  # or whisper.cpp bindings
cooklang>=0.1.0
```

---

## Design Decisions

1. **Spotify auth flow** - One-time setup via SSH. Run auth script once, it opens browser URL, you authenticate on another device, paste the redirect URL back. Token is cached locally.

2. **Wake word** - "Hey Olly"

3. **Timer alarm sound** - Soft beeping pattern (three gentle beeps, repeated)

4. **Recipe source** - Local files only. CookLang `.cook` files in `recipes/` folder, organized by category subfolders.

5. **Ghosting management** - Fixed interval full refresh every 15 partial refreshes.

6. **Volume feedback** - Brief visual indicator in footer region + soft beep.

# Build Progress

**IMPORTANT FOR NEW CLAUDE SESSIONS**: Read this file first! Update it before ending your session.

---

## Quick Start for New Sessions

**What is this?** A Raspberry Pi 5 smart kitchen display with:
- 7.5" e-ink display (Waveshare)
- Voice control ("Hey Ollie" wake word)
- Recipes, timers, weather, Spotify integration

**To run the app:**
```bash
cd /home/casonwight/repos/smart-display
python main.py
```

**Key files to know:**
- `main.py` - Main application loop, state machine, voice command handlers
- `audio/voice.py` - Wake word detection, STT, intent parsing
- `audio/tts.py` - Piper text-to-speech
- `audio/weather.py` - Weather API integration
- `config.py` - All configuration constants

**What works:** Everything! Wake word, voice commands, TTS responses (hfc_female voice), voice status icons, stop-talking, timers, recipes, weather, navigation

**What's broken:** Nothing known - Spotify controls work if credentials are configured (see setup below)

**What's next:** See "Next Steps" section below

---

## Current Status: Spotify API controls implemented (2026-02-21)

## Completed
- [x] Hardware testing (all components working)
- [x] Technical spec (SPEC.md)
- [x] Project structure and dependencies
- [x] Display system (renderer, regions) - test pattern verified by user
- [x] Input handling (encoder, buttons) - working, tested by user
- [x] Home screen app - full-screen wallpaper with soft cloud time overlay
- [x] Menu screen - icons with partial refresh, debounced input
- [x] Recipe app - CookLang parser, categories, recipe list, formatted recipe view with scroll
- [x] Timer app - multiple timers, pause/resume, add time, alarm sound
- [x] Music/Spotify app - Now Playing UI, reads from librespot onevent state file
- [x] Main app loop - state machine, back buttons, volume overlay, timer interrupts, auto-timeouts
- [x] Voice control - wake word + command recognition + TTS responses
- [x] Voice status icons - listening, thinking, talking overlays on e-ink display
- [x] Spotify Web API controls - pause/play/skip via encoder + voice commands

## In Progress
- [ ] Add more otter icons throughout the app (see icon opportunities below)
- [ ] As part of the above, "confused-icon.png" was added to app icons, but not implemented. It should be shown when the voice command is unintelligible.

## Pending
- [ ] Spotify API credentials setup (run `python scripts/spotify_auth.py` after adding credentials to config.py)

## Session Notes (2026-02-21) - Spotify API Controls

### Spotify Web API Integration
- **Spotify API unblocked** - New app registrations re-enabled by Spotify
- **`audio/spotify_api.py`** - New `SpotifyController` class using `spotipy`
  - Reads credentials from `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` env vars (or config.py)
  - Uses cached OAuth token only at startup (non-blocking) - token cached at `~/.cache/spotipy-smart-display`
  - Methods: `pause()`, `resume()`, `toggle_play_pause()`, `next_track()`, `previous_track()`, `play_search()`
  - Device auto-detection: prefers "Kitchen Display" (raspotify), falls back to any active/available device
  - Fails gracefully if not configured (all methods return False/None)
- **`scripts/spotify_auth.py`** - One-time interactive OAuth setup script
  - Prints auth URL, user visits on any device, pastes redirect URL back
  - Token cached for future sessions (spotipy handles refresh automatically)
- **`config.py`** - Added `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `SPOTIFY_CACHE_PATH`
- **`apps/music.py`** - Encoder controls now wired to Spotify API
  - Encoder press: `toggle_play_pause()` (stays in music app)
  - Encoder rotate left/right: `previous_track()` / `next_track()`
  - Hint updated: "Rotate: Skip | Press: Pause/Play" when API available
- **`main.py`** - Voice commands now functional
  - "Hey Ollie, pause / stop the music" → `pause()` or `resume()` based on current state
  - "Hey Ollie, skip" / "next song" → `next_track()`
  - "Hey Ollie, play [song/artist]" → `play_search(query)` with TTS confirmation
  - "Hey Ollie, play" (no query) → `resume()`

### Spotify Setup Steps
1. Create app at https://developer.spotify.com/dashboard
2. Add `http://localhost:8888/callback` as Redirect URI in app settings
3. Set credentials: `export SPOTIFY_CLIENT_ID=xxx SPOTIFY_CLIENT_SECRET=yyy` (or edit config.py)
4. Run: `python scripts/spotify_auth.py` — visit URL on phone, paste redirect back
5. Done! Token auto-refreshes via spotipy cache

### Voice Commands Now Working
| Command | Example | Result |
|---------|---------|--------|
| spotify_play (with query) | "play Shape of You" | Searches + plays, TTS confirms |
| spotify_play (no query) | "play" | Resumes playback |
| spotify_pause | "pause the music" / "stop" | Pause or resume based on state |
| spotify_skip | "skip" / "next song" | Skip to next track |

---

## Session Notes (2026-02-09) - Voice Status Icons, TTS Voice, Speaker Upgrade

### Speaker Upgrade
- **Installed Facmogu 3" 88dB speakers** - replacing the old Gikfun 2" 77dB speakers
- Drop-in replacement, same impedance and form factor, much louder

### TTS Voice Change
- **Switched from `en_US-lessac-medium` to `en_US-hfc_female-medium`** - lighter, friendlier female voice
- Benchmarked on Pi 5: hfc_female ~1.33s avg vs lessac ~1.46s avg (slightly faster)
- Only medium quality available for hfc_female (no high variant on HuggingFace)
- Model already downloaded to `models/piper/`

### Voice Status Icons
- **Added 4 otter voice status icons** to `assets/app-icons/`:
  - `listening-icon.png` - shown when wake word detected, device listening for command
  - `thinking-icon.png` - shown when processing audio (transcribing, parsing intent)
  - `talking-open-icon.png` - mouth open frame for talking animation
  - `talking-closed-icon.png` - mouth closed frame for talking animation
- **Icon overlay system** - white rounded-rect popup centered on screen
  - Icons cropped 15% from all sides (remove whitespace border), thumbnailed to 300px
  - 10px padding in box, ~320x320 overlay centered on 800x480 display
- **Talking animation** - natural mouth movement pattern:
  - Mouth mostly closed with short open bursts (150-250ms open, 400-700ms closed)
  - Randomized timing to avoid mechanical look
- **Status callback architecture**:
  - `VoiceController` gets `on_status_callback` → fires "listening", "thinking", "command_done", "idle"
  - `PiperTTS` gets `on_speaking_changed` → fires True/False for talking state
  - `MainController` draws overlay immediately from background threads using `_lock`
- **Graceful cleanup** - if voice command doesn't trigger TTS, overlay clears after 500ms grace period

### Stop Talking (Encoder Press)
- **Added `stop()` method to PiperTTS** - kills piper/aplay subprocesses immediately
- **Encoder press during TALKING state** stops TTS instantly
- Existing `finally` block handles cleanup: sets `_speaking=False`, fires `on_speaking_changed(False)`, overlay clears

### Bug Fixes
- **Recording cutoff fix** - listening icon display was blocking the voice thread, causing first words to be missed
  - Moved `on_status("listening")` to before the beep
  - Made listening icon display non-blocking (background thread)
  - Recording now starts immediately after beep with no gap
- **Overlay disappearing during talking** - two root causes found and fixed:
  1. Clear-first white refresh was blanking the overlay mid-talk → deferred clear-first while voice overlay is active
  2. Home screen time update and music progress update were doing direct partial refreshes that bypassed `_render_internal()` → skipped during voice overlay
- **Overlay flash on dismiss removed** - no longer does clear-first when overlay goes to IDLE (avoids full screen flash)

### Files Modified
- `main.py` - VoiceOverlayState enum, icon loading/cropping, overlay drawing, status/TTS callbacks, talking animation, stop-on-press, deferred clear-first, skip direct refreshes during overlay
- `audio/voice.py` - Added `on_status_callback` parameter, status calls at key flow points, non-blocking listening icon
- `audio/tts.py` - Added `on_speaking_changed` callback, `stop()` method, switched to hfc_female voice
- Moved icons from `assets/` to `assets/app-icons/`

---

## Session Notes (2026-02-05) - Voice Robustness & Otter Theme

### Voice Command Improvements
- **Added "refresh screen" voice command** - Does deep refresh to clear e-ink ghosting
  - On home screen: reloads wallpaper + clear-first refresh
  - On other screens: white full refresh + content full refresh
- **Extensive misheard variants added to ALL voice commands**
  - "timer" → timer, time or, time her, tamer, timor, tie more, dimer, time are, tie mer
  - "pause" → pause, paws, paused, pos, pours, cause, pas, poss, pawns
  - "home" → home, hone, ohm, comb, dome, foam, roam, holm
  - "menu" → menu, men you, venue, ben you, many you, manu
  - "refresh" → refresh, we fresh, we flash, we slash, please fresh, read flash, etc.
  - See `audio/voice.py` IntentParser.PATTERNS for full list
- **Trailing punctuation stripped globally** - Whisper sometimes adds periods
- **"Current recipe" context improved** - Only valid when:
  1. Actually viewing a recipe (RecipeState.RECIPE_VIEW), OR
  2. Exactly one active timer with a recipe label

### Music App Fixes
- **Progress bar now updates every second** - Partial refresh of progress region
- **Thread safety added** - Lock prevents concurrent display operations
- **Auto-jump rendering fixed** - Skips progress updates during clear-first

### Otter Theme Progress
- **Timer alarm screen** - Added timer-done-icon.png (150x150, displayed on alarm popup)
- **Delete recipe confirmation** - Already has sad-icon.png

### Otter Icon Opportunities Identified
Places that could use otter icons (for future):
| Location | Current State | Suggested Icon |
|----------|---------------|----------------|
| Music app - no music | Generic ♪ | Otter with headphones |
| Timers app - empty | Blank | Relaxed/lounging otter |
| Recipes app - empty | Blank | Otter chef |
| Album art placeholder | Generic ♪ | Otter with vinyl |
| No recipes found | Nothing | Otter with magnifying glass |

### Files Modified
- `main.py` - Deep refresh, music progress threading, context recipe logic
- `audio/voice.py` - All voice command patterns expanded with misheard variants
- `apps/timers.py` - Timer done icon on alarm screen
- `apps/music.py` - Progress bar partial refresh (already existed, now working)

---

## Session Notes (2026-02-04) - Voice Control Implementation

### Voice Control System
- **Wake word detection**: Porcupine with custom "Hey Ollie" model
- **Speech-to-text**: faster-whisper (base.en model)
- **Text-to-speech**: Piper (standalone binary) - fast neural TTS with natural voice
- **Intent parsing**: Regex-based pattern matching with word number support

### Voice Commands Implemented
| Command | Example Phrases | Status |
|---------|-----------------|--------|
| timer_start | "start a timer for 5 minutes" | ✅ Working |
| timer_stop | "cancel the timer" | ✅ Working |
| timer_stop_all | "cancel all timers" | ✅ Working |
| timer_status | "how much time is left?" | ✅ Working |
| timer_add_time | "add 5 minutes to the timer" | ✅ Working |
| recipe_show | "show me the pancakes recipe" | ✅ Working |
| recipe_ingredients | "what's in the pancakes recipe" | ✅ Working |
| category_browse | "show me desserts" / "what's for dinner?" | ✅ Working |
| go_home | "go home" | ✅ Working |
| go_back | "go back" | ✅ Working |
| open_menu | "open menu" | ✅ Working |
| open_timers/recipes/music | "open timers" | ✅ Working |
| weather | "what's the weather?" | ✅ Working |
| temperature | "what's the temperature?" | ✅ Working |
| time | "what time is it?" | ✅ Working |
| date | "what's the date?" / "what day is today?" | ✅ Working |
| refresh_screen | "refresh the screen" / "clear ghosting" / "fix the display" | ✅ Working |
| spotify_play/pause/skip | (various) | ❌ Blocked (Spotify API unavailable) |

### New Files Created
- `audio/tts.py` - Piper TTS wrapper
- `audio/weather.py` - Weather API (Open-Meteo) + IP geolocation
- `models/piper/` - Piper model files (auto-downloaded from HuggingFace)

### Key Technical Details
- **Piper TTS**: Uses standalone Piper binary (not Python package - broken on Python 3.13)
  - Voice: "en_US-lessac-medium" (natural US English)
  - Streams raw audio directly to aplay via pipe to plughw:2,0
  - Fast response (~0.5s latency vs ~3s for Kokoro)
- **Weather API**: Open-Meteo (free, no API key) + ip-api.com for location
- **Recipe search**: Word-by-word matching with scoring (handles partial matches)
- **Word numbers**: Supports "five", "twenty", "thirty-five" etc. in timer commands

### Issues Fixed
- Recipe search: Now uses word-by-word matching instead of substring
- Recipe name cleaning: Strips possessives ('s), filters noise words (the, a, which)
- Number parsing: Whisper transcribes "5" as "five" - added word-to-number conversion
- Audio routing: TTS must use plughw:2,0 (not hw:2,0) for format conversion
- Amp enable: GPIO pins 22,23 must be set high before TTS playback

### Known Issues
- piper-tts Python package broken on Python 3.13 - use standalone binary instead
- Download from https://github.com/rhasspy/piper/releases (piper_arm64.tar.gz)
- Install to ~/bin/ and set LD_LIBRARY_PATH=~/bin

---

## Session Notes (2026-01-27)

### Issues Fixed This Session
- **Volume beep removed** - No more annoying beep when changing volume
- **Volume overlay redesigned** - Now at bottom center with 7 dots that grow based on volume
  - Dots grow outward from center: vol 1 = only middle dot big, vol 10 = all dots big
  - Number displayed above the dots
- **Volume overlay rendering** - Fixed scrambled display by drawing on framebuffer before display
- **Timer alarm stops on dismiss** - Added `stop_playback()` to AudioPlayer
- **Timer alarm add-time feature** - Can now spin encoder during alarm to add time, press to confirm
- **Delete All Timers option** - Added to timer list menu
- **Delete All Timers scroll fix** - Resets scroll_offset to 0 after deleting
- **Timer detail screen** - Replaced Pause button with Back button (Hold always goes to Home now)
- **Recipe view** - Removed description, now shows only ingredients and steps
- **Music back button** - Wider box (105px), shorter hint ("Hold: Home")
- **Audio MP3 support** - AudioPlayer now uses ffplay for MP3 files (alarm sound works)

### E-Ink Display Quirks (Important!)
- **Partial refresh limitations**: E-ink partial refresh cannot fully switch dark pixels to white
- **Full refresh required for**:
  - HOME → MENU transition (dark wallpaper → white menu)
  - Entering MUSIC app (text ghosting causes overlap without full refresh)
  - Returning to HOME (good time to clear ghosting, wallpaper masks blink)
- **After full refresh**: Must call `display_Partial()` again after `init_partial()` to show image correctly
- **Progress bar partial refresh disabled**: Was causing display artifacts

### Known Issues Fixed
- **Menu icons not showing** - Fixed with "clear-first" approach: does a white partial refresh before content when leaving home
- **Music text overlap** - ACTUALLY FIXED: The Spotify state file contains newline characters (`\n`) between multiple artists. PIL was rendering these as actual line breaks, causing text to wrap and overlap. Fix: replace `\n` with `, ` before truncating/rendering.
- **Home going white after full refresh** - Fixed by not calling init_partial() immediately after full refresh; call it lazily before next partial refresh
- **Timer scroll arrow in selection box** - Made item boxes narrower (leave 50px for scroll indicators)
- **Timer alarm sound not stopping** - Fixed by using SIGKILL and pkill for ffplay processes

### To Test Next
- Verify menu icons show with clear-first (no full blink)
- Verify music text no longer overlaps
- Verify home screen shows correctly after full refresh
- Verify timer scroll arrows are outside boxes
- New volume overlay with 7 growing dots
- Timer detail with Back button instead of Pause
- Recipe view without description

---

## Session Notes (2026-01-22)

### Main Loop Features Implemented
- **Back buttons**: All apps (Recipe, Timer, Music) now have "← Back" buttons
  - Recipe/Timer: Back button at index 0 in list views
  - Music: Back button in footer, press to return to menu
- **Long press encoder**: Goes to Home from anywhere
- **Volume overlay**: Shows volume level 1-10 with partial refresh, auto-hides after 2 seconds
- **Timer alarm interrupt**: Takes over any screen, press to dismiss and return to previous screen
- **Auto-timeouts**: Menu (1min), Music paused (1min), Timers idle (1min) → return to Home
- **Spotify auto-switch**: When music starts playing, auto-switches to Music app

### Bug Fixed During Testing
- Progress bar crash when fill_width < 5px (x1 < x0 error) - fixed by checking `fill_width > 4`

### To Test Next Session
- Verify all navigation flows work correctly
- Test timer alarm interrupt and return to previous screen
- Test volume overlay appearance and auto-hide
- Test auto-timeouts (may need to wait 1+ minute idle)
- Test Spotify connect auto-switch to Music app

---

## Hardware Notes

### Speaker Upgrade (Installed!)
- **Old**: Gikfun 2" 77dB speakers - too quiet
- **New**: Facmogu 3" 4-Ohm 88dB speakers (~12x louder) - installed 2026-02-09
- Same impedance and form factor, drop-in replacement

### MAX98357A Gain Configuration
- GAIN pin connected to GND (Pin 14) = 9 dB gain

## Hardware TODO
- [x] Replace speakers with bigger ones (Facmogu 3" 88dB) - installed 2026-02-09
- [ ] Add stereo resistors: 100K ohm (LEFT/Amp2), 330K ohm (RIGHT/Amp1) - see TESTING_NOTES.md
- [ ] Design case for 3D printing

## Future Enhancements
- [x] ~~Cuter TTS voice~~ - Switched to `en_US-hfc_female-medium` (2026-02-09)
- [ ] Full Spotify API integration (currently on hold - new integrations paused by Spotify)
  - Play/pause/skip controls from the display
  - Search and play tracks by voice
  - Browse playlists and albums
  - Queue management

## Recipe Import via iOS Share Sheet

Import recipes from any website by sharing the URL from your iPhone.

### Setup

1. Install dependencies:
   ```bash
   pip install flask recipe-scrapers
   ```

2. Start the server (for testing):
   ```bash
   python scripts/recipe_server.py
   ```

3. Install as systemd service (auto-start on boot):
   ```bash
   sudo cp scripts/recipe-server.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable recipe-server
   sudo systemctl start recipe-server
   ```

4. Create iOS Shortcut:
   - Open Shortcuts app
   - Create new shortcut
   - Enable "Show in Share Sheet" → accept URLs
   - Add "Menu" action with categories: Main Dishes, Sides, Desserts, Breakfast
   - Add "Get Contents of URL" action:
     - URL: `http://<pi-ip>:5050/api/recipe`
     - Method: POST
     - Headers: Content-Type = application/json
     - Request Body: JSON with "url" and "category" fields
   - Add "If" to check success, show appropriate alert

### API Endpoints

- `GET /api/categories` - List available categories
- `POST /api/recipe` - Import recipe
  - Body: `{"url": "https://...", "category": "Main Dishes"}`
  - Returns: `{"success": true, "recipe": "Name", "path": "..."}`

### Supported Sites

Uses `recipe-scrapers` library which supports 606+ recipe websites including:
AllRecipes, BBC Good Food, Food Network, Serious Eats, etc.

---

## Files Created

```
smart-display/
├── config.py                 # Configuration constants
├── requirements.txt          # Python dependencies
├── test_display.py           # Display test script
├── test_input.py             # Input test script
├── test_audio.py             # Audio test script
├── test_home.py              # Home screen test
├── test_menu.py              # Menu test
├── test_recipes.py           # Recipe app test
├── test_timers.py            # Timer app test
├── test_music.py             # Music app test
├── display/
│   ├── __init__.py
│   ├── renderer.py           # DisplayRenderer class
│   └── regions.py            # Region definitions
├── input/
│   ├── __init__.py
│   ├── encoder.py            # RotaryEncoder class
│   └── buttons.py            # VolumeButtons class
├── audio/
│   ├── __init__.py
│   ├── player.py             # AudioPlayer class
│   ├── voice.py              # VoiceController (wake word + STT + intent parsing)
│   ├── tts.py                # PiperTTS (text-to-speech)
│   └── weather.py            # Weather API integration
├── apps/
│   ├── __init__.py
│   ├── home.py               # HomeApp class
│   ├── menu.py               # MenuApp class
│   ├── recipes.py            # RecipeApp class
│   ├── timers.py             # TimerApp class
│   └── music.py              # MusicApp class
├── scripts/
│   ├── spotify_event.py      # Librespot onevent callback
│   ├── recipe_server.py      # Recipe import webhook server
│   ├── cooklang_converter.py # Recipe-to-CookLang converter
│   └── recipe-server.service # Systemd service file
├── recipes/                  # CookLang recipe files
├── models/
│   ├── Hey-Ollie_en_raspberry-pi_v4_0_0.ppn  # Porcupine wake word model
│   ├── piper/
│   │   ├── en_US-lessac-medium.onnx          # Piper TTS model (auto-downloaded)
│   │   └── en_US-lessac-medium.onnx.json     # Piper config
│   └── vosk-model-small-en-us-0.15/         # (unused, was for Vosk STT)
└── assets/
    ├── otter-wallpaper.jpg   # Home screen background
    └── alarm-noise.mp3       # Timer alarm sound
```

---

## Testing Checkpoints

| # | Component | Status | Notes |
|---|-----------|--------|-------|
| 1 | Display renderer | PASS | Test pattern showed correctly |
| 2 | Input handling | PASS | Encoder rotation, press, long-press, buttons all work |
| 3 | Audio playback | PASS | alarm-noise.mp3 plays correctly |
| 4 | Home screen | PASS | Full-screen wallpaper + soft cloud time overlay |
| 5 | Menu navigation | PASS | Icons, partial refresh, debounced encoder input |
| 6 | Recipe app | PASS | CookLang parsing, categories, scrollable formatted view |
| 7 | Timer app | PASS | Multiple timers, pause/resume, add time, alarm sound |
| 8 | Music app | PASS | Now Playing UI, album art, progress partial refresh |
| 9 | Main loop | PASS | State machine, navigation, timeouts all working |
| 10 | Wake word | PASS | Porcupine "Hey Ollie" detection working |
| 11 | Speech-to-text | PASS | faster-whisper transcription working |
| 12 | Intent parsing | PASS | All voice commands recognized |
| 13 | Text-to-speech | PASS | Piper TTS fast and natural |
| 14 | Weather API | PASS | Open-Meteo returns correct data |

---

## Key Technical Details

### Audio Device
- Speakers are on **card 2** (MAX98357A), not card 0
- Use `hw:2,0` for aplay/speaker-test
- Config: `AUDIO_DEVICE = "hw:2,0"` in config.py

### Display
- Import: `from waveshare_epd import epd7in5_V2`
- Resolution: 800x480
- Supports partial refresh via `display_Partial()`
- **IMPORTANT**: Use `renderer.init_partial()` to avoid black/white flashing
- Must generate **stereo** WAV files (mono fails)

### GPIO Summary
- Encoder: A=5, B=6, Switch=13
- Buttons: Up=26, Down=16
- Amp SD pins: 22, 23 (drive high to enable)
- I2S BCLK: 18 (set to a2 mode)
- Amp GAIN: Pin 14 (GND, 9 dB)

### Spotify Connect
- Raspotify service: `sudo systemctl status raspotify`
- Config file: `/etc/raspotify/conf`
- Device name: "Kitchen Display"
- Onevent callback writes to: `/tmp/spotify_state.json`
- Spotify API unavailable (new integrations paused) - using librespot onevent for track metadata
- **Important**: Systemd override at `/etc/systemd/system/raspotify.service.d/onevent.conf` disables sandboxing so the onevent script can write to `/tmp`
- Album art downloaded from Spotify CDN, dithered to 1-bit for e-ink display

---

## User Feedback (Important for Final Implementation)

1. **No full refresh on every change** - causes visible black/white flash
   - Use partial refresh for UI updates
   - Use `renderer.init_partial()` at startup

2. **Batch encoder input** - don't update display on every click
   - Use debounce with 250ms delay
   - Skip intermediate states

3. **Button press vs hold** - use when_pressed/when_released/when_held pattern
   - Track button_held flag to distinguish press from hold

4. **State change cooldown** - prevent double-triggers
   - Add cooldown (0.3s) on state transitions

---

## How to Test Components

```bash
# Display test
python3 test_display.py

# Input test (interactive)
python3 test_input.py

# Audio test
python3 test_audio.py

# Home screen test
python3 test_home.py

# Menu test
python3 test_menu.py

# Recipe app test
python3 test_recipes.py

# Timer app test (5 min)
python3 test_timers.py

# Direct speaker test
pinctrl set 18 a2 && pinctrl set 22,23 op dh
speaker-test -D hw:2,0 -t sine -f 440 -c 2 -l 1

# Test Spotify Connect
# Open Spotify app on phone, look for "Kitchen Display" device
```

---

## Next Steps

1. **Final integration testing** - Test all voice commands end-to-end, verify voice status icons
2. ~~**Install larger speakers**~~ - Done! Facmogu 3" 88dB speakers installed
3. **Add stereo resistors** - 100K (LEFT), 330K (RIGHT) for stereo separation
4. **Design and 3D print case**
5. **Spotify API** - Wait for Spotify to re-enable new integrations

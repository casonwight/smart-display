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
- `audio/tts.py` - Kokoro text-to-speech
- `audio/weather.py` - Weather API integration
- `config.py` - All configuration constants

**What works:** Everything! Wake word, voice commands, TTS responses, timers, recipes, weather, navigation

**What's broken:** Spotify play/pause/skip (Spotify API blocked for new integrations)

**What's next:** See "Next Steps" section below

---

## Current Status: Voice control working! (2026-02-04)

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

## In Progress
- [ ] Final integration testing of voice commands

## Pending
- [ ] Spotify API integration (blocked - Spotify paused new integrations)
- [ ] Update this and other documents to note the switch from Kokoro to piper (look up code, remove/replace kokoro references and files, clean things up)
- [ ] Bigger icons for all 3 apps (recipes, timers, music), but even more cropping for the latter 2 as well (after checking that the crop does not cut off non-white portions of the 2 images)
- [ ] Display formatting issues for recipes (parentheses after text should have space, e.g. "butter(melted)" -> "butter (melted)" and fix problems like "@?sugar" in the molasses cookies recipe, and still adhere to cooklang documentation)
- [ ] When a timer is selected from a recipe, the buttons turns dark and the recipe is added. This might be a mistaken push, so I want it so that if the user clicks again, it "unclicks it" (resets to normal white button and the timer is deleted)
- [ ] The left scroll arrow next to the bacl button and timers on a recipe page is too close. That left arrow indicating scrolling is overlapping a little bit
- [ ] The progress bar on music can be updated every second (partial refresh of the eink display). So can the "time in song" number. This should be done only as a partial refresh on the screen
- [ ] The "Down arrow" for scrolling on the recipes menu pages (both recipe types and the lists of recipes) is too close to the horizontal bar at the bottom

## Session Notes (2026-02-04) - Voice Control Implementation

### Voice Control System
- **Wake word detection**: Porcupine with custom "Hey Ollie" model
- **Speech-to-text**: faster-whisper (base.en model)
- **Text-to-speech**: Kokoro (kokoro-onnx) - natural sounding neural TTS
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
| spotify_play/pause/skip | (various) | ❌ Blocked (Spotify API unavailable) |

### New Files Created
- `audio/tts.py` - Kokoro TTS wrapper
- `audio/weather.py` - Weather API (Open-Meteo) + IP geolocation
- `models/kokoro/` - Kokoro model files

### Key Technical Details
- **Kokoro TTS**: Uses kokoro-onnx package with int8 quantized model (~92MB)
  - Voice: "af_heart" (natural female voice)
  - Generates WAV, plays via aplay to plughw:2,0
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
- Piper TTS sounds robotic - replaced with Kokoro
- Kokoro (PyTorch version) won't install on ARM64 - use kokoro-onnx instead
- onnxruntime GPU warning on Pi (harmless, uses CPU)

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

### Speaker Upgrade (Ordered)
- **Current**: Gikfun 2" 77dB speakers - too quiet
- **Ordered**: Facmogu 3" 4-Ohm 88dB speakers (~12x louder)
- Same impedance and form factor, drop-in replacement

### MAX98357A Gain Configuration
- GAIN pin connected to 3V3 (Pin 17) for maximum 15dB gain
- Alternative: Pin 14 (GND) for 9dB gain, or float for 12dB

## Hardware TODO
- [x] Replace speakers with bigger ones (Facmogu 3" 88dB)
- [ ] Add stereo resistors: 100K ohm (LEFT/Amp2), 330K ohm (RIGHT/Amp1) - see TESTING_NOTES.md
- [ ] Design case for 3D printing

## Future Enhancements
- [ ] Full Spotify API integration (currently on hold - new integrations paused by Spotify)
  - Play/pause/skip controls from the display
  - Search and play tracks by voice
  - Browse playlists and albums
  - Queue management
- [ ] Recipe import via SMS/text message
  - Text a recipe URL to the smart display
  - System uses https://cook.md/ API to convert to CookLang format
  - Flow:
    1. User texts just a URL (any recipe website)
    2. System attempts conversion via cook.md
    3. If success: replies with numbered category list ("1 - Main Dishes", "2 - Desserts", etc.)
    4. User replies with number to select category
    5. Recipe saved to `recipes/<category>/<recipe-name>.cook`
    6. If failure: friendly error message
  - Research needed: SMS gateway (Twilio? email-to-SMS? (stretch) another iphone-friendly way to simply click "Share To" and one of the icons is the smart display?)

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
│   ├── tts.py                # KokoroTTS (text-to-speech)
│   └── weather.py            # Weather API integration
├── apps/
│   ├── __init__.py
│   ├── home.py               # HomeApp class
│   ├── menu.py               # MenuApp class
│   ├── recipes.py            # RecipeApp class
│   ├── timers.py             # TimerApp class
│   └── music.py              # MusicApp class
├── scripts/
│   └── spotify_event.py      # Librespot onevent callback
├── recipes/                  # CookLang recipe files
├── models/
│   ├── Hey-Ollie_en_raspberry-pi_v4_0_0.ppn  # Porcupine wake word model
│   ├── kokoro/
│   │   ├── kokoro-v1.0.int8.onnx            # Kokoro TTS model
│   │   └── voices-v1.0.bin                   # Kokoro voice data
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
| 13 | Text-to-speech | PASS | Kokoro TTS sounds natural |
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
- Amp GAIN: 17 (3V3 for max gain)

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

1. **Final integration testing** - Test all voice commands end-to-end
2. **Install larger speakers** - Facmogu 3" 88dB speakers (ordered)
3. **Add stereo resistors** - 100K (LEFT), 330K (RIGHT) for stereo separation
4. **Design and 3D print case**
5. **Spotify API** - Wait for Spotify to re-enable new integrations

#!/usr/bin/env python3
# Suppress ONNX Runtime GPU discovery warning on Pi (must be before any imports)
import os
os.environ["ORT_LOG_LEVEL"] = "3"
os.environ["ONNXRUNTIME_LOG_SEVERITY_LEVEL"] = "3"

"""
Smart Display - Main Application Loop

Ties together all components: display, input, audio, and apps.
Manages state machine, volume overlay, timer alarms, and auto-timeouts.
"""

import time
import uuid
import threading
from enum import Enum
from typing import Optional, Callable
from datetime import datetime
from pathlib import Path
from gpiozero import RotaryEncoder, Button
from PIL import Image, ImageDraw, ImageFont

from display.renderer import DisplayRenderer
from audio.player import AudioPlayer
from audio.spotify_api import SpotifyController
from audio.voice import VoiceController
from audio.tts import create_tts
from audio.weather import get_weather, format_weather_speech, format_temperature_speech
from apps.home import HomeApp
from apps.menu import MenuApp, MenuItem
from apps.recipes import RecipeApp, RecipeState
from apps.timers import TimerApp, TimerState
from apps.music import MusicApp, MusicState, STATE_FILE as SPOTIFY_STATE_FILE
from config import (
    ENCODER_PIN_A, ENCODER_PIN_B, ENCODER_PIN_SW,
    BUTTON_UP_PIN, BUTTON_DOWN_PIN,
    DISPLAY_WIDTH, DISPLAY_HEIGHT,
    VOLUME_STEP, ASSETS_DIR
)


class AppState(Enum):
    """Main application states."""
    HOME = "home"
    MENU = "menu"
    RECIPES = "recipes"
    TIMERS = "timers"
    MUSIC = "music"


class VoiceOverlayState(Enum):
    """Voice interaction overlay states."""
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    TALKING = "talking"
    CONFUSED = "confused"


class MainController:
    """Main application controller - manages state and coordinates all components."""

    # Timeout durations (seconds)
    MENU_TIMEOUT = 60          # Return to home after 1 min idle in menu
    MUSIC_PAUSE_TIMEOUT = 60   # Return to home after 1 min of paused/stopped music
    TIMERS_IDLE_TIMEOUT = 60   # Return to home after 1 min idle with no active timers
    VOLUME_DISPLAY_TIME = 2    # Show volume overlay for 2 seconds

    # Debounce
    DEBOUNCE_DELAY = 0.25      # 250ms debounce for encoder
    STATE_CHANGE_COOLDOWN = 0.3  # 300ms cooldown after state changes

    def __init__(self):
        print("Initializing Smart Display...")

        # Initialize display
        print("  - Display...")
        self.renderer = DisplayRenderer()
        self.renderer.init()
        self.renderer.clear()
        self.renderer.init_partial()

        # Initialize audio
        print("  - Audio...")
        self.audio = AudioPlayer()

        # Initialize apps
        print("  - Apps...")
        self.home_app = HomeApp(self.renderer)
        self.menu_app = MenuApp(self.renderer)
        self.recipe_app = RecipeApp(self.renderer)
        self.timer_app = TimerApp(self.renderer, self.audio)
        self.music_app = MusicApp(self.renderer)

        # Initialize Spotify API controller (non-blocking - uses cached token only)
        print("  - Spotify...")
        self.spotify = SpotifyController()
        self.music_app.spotify = self.spotify

        # Mini player fonts
        try:
            self._mini_font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            self._mini_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
        except OSError:
            self._mini_font_bold = ImageFont.load_default()
            self._mini_font = ImageFont.load_default()

        # State management
        self.state = AppState.HOME
        self.previous_state: Optional[AppState] = None  # For timer alarm return
        self._last_state_change = time.time()
        self._needs_full_refresh = False
        self._partial_refresh_count = 0
        self._max_partial_before_full = 9999  # Effectively disable auto full refresh - use clear-first instead
        self._needs_clear_first = False  # Clear screen with white before rendering
        self._skip_next_home_update = False  # Skip home update after full refresh
        self._full_refresh_cooldown_until = 0  # Timestamp - no partial refresh until this time

        # Timer alarm state
        self.timer_alarm_active = False
        self.timer_alarm_previous_state: Optional[AppState] = None

        # Volume overlay state
        self.volume_overlay_active = False
        self.volume_overlay_hide_time = 0

        # Voice overlay state
        self._voice_overlay_state = VoiceOverlayState.IDLE
        self._voice_overlay_icons = self._load_voice_icons()
        self._talking_mouth_open = True
        self._talking_animation_timer: Optional[threading.Timer] = None

        # Activity tracking for timeouts
        self.last_activity_time = time.time()
        self.last_music_playing_time = time.time()

        # Spotify connection tracking
        self._last_spotify_connected = False
        self._last_spotify_check = 0
        self._last_known_track = ""  # For on_new_track detection
        self._mini_player_tick = 0   # Rate-limits mini player progress refresh on MENU

        # Debounce
        self._pending_update = threading.Event()
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._last_encoder_press_time = 0.0  # For rotation bounce suppression

        # Set up callbacks
        self.timer_app.on_update = self._on_timer_update
        self.music_app.on_update = self._on_music_update
        self.music_app.on_progress_update = self._on_music_progress_update

        # Override timer alarm handling
        self._original_timer_alarm_handler = self.timer_app._on_timer_complete
        self.timer_app._on_timer_complete = self._on_timer_alarm

        # Input setup
        print("  - Input...")
        self._setup_input()

        # Register volume overlay region (bottom center with dots)
        self.renderer.add_region("volume_overlay", 200, DISPLAY_HEIGHT - 100, 400, 90)

        # Initialize text-to-speech
        print("  - Text-to-speech...")
        self.tts = create_tts()
        self.tts.on_speaking_changed = self._on_tts_speaking_changed

        # Initialize voice control
        print("  - Voice control...")
        self.voice = VoiceController(
            on_command_callback=self._on_voice_command,
            audio_player=self.audio,
            on_status_callback=self._on_voice_status,
        )
        self.voice.start()

        print("Initialization complete!")

    def _setup_input(self):
        """Set up encoder and button inputs."""
        self.encoder = RotaryEncoder(ENCODER_PIN_A, ENCODER_PIN_B, max_steps=0)
        self.encoder_button = Button(ENCODER_PIN_SW, hold_time=1.0)
        self.button_up = Button(BUTTON_UP_PIN)
        self.button_down = Button(BUTTON_DOWN_PIN)

        # Button state tracking
        self._encoder_held = False

        # Encoder rotation
        self.encoder.when_rotated_clockwise = lambda: self._on_rotate(-1)
        self.encoder.when_rotated_counter_clockwise = lambda: self._on_rotate(1)

        # Encoder button
        self.encoder_button.when_pressed = self._on_encoder_press
        self.encoder_button.when_released = self._on_encoder_release
        self.encoder_button.when_held = self._on_encoder_hold

        # Volume buttons
        self.button_up.when_pressed = self._on_volume_up
        self.button_down.when_pressed = self._on_volume_down

    def _record_activity(self):
        """Record user activity for timeout tracking."""
        self.last_activity_time = time.time()

    def _schedule_update(self):
        """Schedule a debounced display update."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(self.DEBOUNCE_DELAY, self._do_update)
            self._debounce_timer.start()

    def _do_update(self):
        """Signal that an update is needed."""
        self._pending_update.set()

    # ==================== Input Handlers ====================

    def _on_rotate(self, direction: int):
        """Handle encoder rotation."""
        # Suppress mechanical bounce from encoder button press/release
        # (pressing the shaft often generates spurious rotation pulses)
        if time.time() - self._last_encoder_press_time < 0.4:
            return

        self._record_activity()

        # During timer alarm, rotation adjusts add-time
        if self.timer_alarm_active:
            self.timer_app.navigate(direction)
            self._schedule_update()
            return

        # Dismiss volume overlay on rotation
        if self.volume_overlay_active:
            self.volume_overlay_active = False

        if self.state == AppState.HOME:
            # Any rotation goes to menu
            self._change_state(AppState.MENU)
        elif self.state == AppState.MENU:
            self.menu_app.navigate(direction)
        elif self.state == AppState.RECIPES:
            self.recipe_app.navigate(direction)
        elif self.state == AppState.TIMERS:
            self.timer_app.navigate(direction)
        elif self.state == AppState.MUSIC:
            self.music_app.navigate(direction)

        self._schedule_update()

    def _on_encoder_press(self):
        """Handle encoder button press start."""
        self._encoder_held = False
        self._last_encoder_press_time = time.time()

    def _on_encoder_release(self):
        """Handle encoder button release (short press)."""
        if self._encoder_held:
            return  # Was a hold, not a press

        self._last_encoder_press_time = time.time()  # Reset on release for post-release bounce
        self._record_activity()

        # Stop TTS if currently talking
        if self._voice_overlay_state == VoiceOverlayState.TALKING:
            self.tts.stop()
            return

        # Timer alarm: dismiss and return to previous screen
        if self.timer_alarm_active:
            self._dismiss_timer_alarm()
            self._schedule_update()
            return

        # Dismiss volume overlay
        if self.volume_overlay_active:
            self.volume_overlay_active = False

        # Check cooldown
        if time.time() - self._last_state_change < self.STATE_CHANGE_COOLDOWN:
            return

        if self.state == AppState.HOME:
            # Press on home goes to menu
            self._change_state(AppState.MENU)
        elif self.state == AppState.MENU:
            selected = self.menu_app.select()
            if selected == MenuItem.RECIPES:
                self._change_state(AppState.RECIPES)
            elif selected == MenuItem.TIMERS:
                self._change_state(AppState.TIMERS)
            elif selected == MenuItem.MUSIC:
                self._change_state(AppState.MUSIC)
        elif self.state == AppState.RECIPES:
            # select() returns False (back to menu), True (handled), or (seconds, label, action) for timer
            result = self.recipe_app.select()
            if result is False:
                self._change_state(AppState.MENU)
            elif isinstance(result, tuple):
                # Timer button pressed - add or remove timer
                seconds, label, action = result
                if action == "add":
                    self._create_timer_from_recipe(seconds, label)
                elif action == "remove":
                    self._remove_timer_from_recipe(seconds, label)
        elif self.state == AppState.TIMERS:
            # select() returns False when back button pressed
            if not self.timer_app.select():
                self._change_state(AppState.MENU)
        elif self.state == AppState.MUSIC:
            # select() returns False to go back to menu
            if not self.music_app.select():
                self._change_state(AppState.MENU)

        self._schedule_update()

    def _on_encoder_hold(self):
        """Handle encoder long press - go to home."""
        self._encoder_held = True
        self._record_activity()

        # Timer alarm: dismiss and return to previous screen
        if self.timer_alarm_active:
            self._dismiss_timer_alarm()
            self._schedule_update()
            return

        # Dismiss volume overlay
        if self.volume_overlay_active:
            self.volume_overlay_active = False

        # Long press always goes to home
        if self.state != AppState.HOME:
            self._change_state(AppState.HOME)
            self._schedule_update()

    def _on_volume_up(self):
        """Handle volume up button. If both buttons pressed simultaneously, toggle play/pause."""
        if self.button_down.is_pressed:
            self._on_both_volume_buttons()
            return
        self._record_activity()
        new_volume = self.audio.volume_up()
        self.tts.volume = new_volume
        self._show_volume_overlay(new_volume)

    def _on_volume_down(self):
        """Handle volume down button. If both buttons pressed simultaneously, toggle play/pause."""
        if self.button_up.is_pressed:
            self._on_both_volume_buttons()
            return
        self._record_activity()
        new_volume = self.audio.volume_down()
        self.tts.volume = new_volume
        self._show_volume_overlay(new_volume)

    def _on_both_volume_buttons(self):
        """Handle simultaneous volume button press — toggle Spotify play/pause."""
        self._record_activity()
        if self.spotify and self.spotify.available:
            is_playing = self.music_app.is_playing
            print(f"[Main] Both volume buttons → toggle play/pause (currently {'playing' if is_playing else 'paused'})")
            self.spotify.toggle_play_pause(is_playing)

    def _create_timer_from_recipe(self, seconds: int, label: str):
        """Create a timer from a recipe timer button."""
        MAX_TIMERS = 5

        if len(self.timer_app.timers) >= MAX_TIMERS:
            print(f"  [Recipe timer: Max timers reached ({MAX_TIMERS})]")
            # Stay on recipe view - user will see the timer list is full
            return

        from apps.timers import Timer
        timer = Timer(
            id=str(uuid.uuid4()),
            total_seconds=seconds,
            remaining_seconds=seconds,
            label=label
        )
        self.timer_app.timers.append(timer)
        print(f"  [Recipe timer: Added {seconds//60}m timer '{label}']")

    def _remove_timer_from_recipe(self, seconds: int, label: str):
        """Remove a timer that was created from a recipe timer button."""
        # Find timer by label and duration
        for timer in self.timer_app.timers:
            if timer.label == label and timer.total_seconds == seconds:
                self.timer_app.timers.remove(timer)
                print(f"  [Recipe timer: Removed {seconds//60}m timer '{label}']")
                return
        print(f"  [Recipe timer: Timer not found '{label}']")

    # ==================== Voice Command Handler ====================

    def _ordinal_list(self, count: int) -> str:
        """Generate a spoken list of ordinals: 'first or second' or 'first, second, or third'."""
        ordinals = ["first", "second", "third", "fourth", "fifth"][:count]
        if len(ordinals) == 1:
            return ordinals[0]
        elif len(ordinals) == 2:
            return f"{ordinals[0]} or {ordinals[1]}"
        else:
            return ", ".join(ordinals[:-1]) + f", or {ordinals[-1]}"

    def _on_voice_command(self, intent: str, params: dict):
        """Handle recognized voice commands."""
        # Don't process voice commands during timer alarm
        if self.timer_alarm_active:
            print(f"  [Voice command ignored - timer alarm active]")
            return

        self._record_activity()

        # === TIMER COMMANDS ===
        MAX_TIMERS = 5

        if intent == "timer_start":
            # Check timer limit
            if len(self.timer_app.timers) >= MAX_TIMERS:
                print(f"  [Voice: Max timers reached ({MAX_TIMERS})]")
                self.tts.speak_async(f"You already have {MAX_TIMERS} timers. Cancel one first.")
                self._change_state(AppState.TIMERS)
                self._schedule_update()
            else:
                # Create and start a timer (use exact seconds from parser)
                total_seconds = params.get("seconds", params.get("minutes", 5) * 60)
                if total_seconds >= 60:
                    print(f"  [Voice: Starting {total_seconds // 60} minute timer]")
                else:
                    print(f"  [Voice: Starting {total_seconds} second timer]")
                from apps.timers import Timer
                timer = Timer(
                    id=str(uuid.uuid4()),
                    total_seconds=total_seconds,
                    remaining_seconds=total_seconds
                )
                self.timer_app.timers.append(timer)
                self._change_state(AppState.TIMERS)
                self._schedule_update()

        elif intent == "timer_stop":
            # Stop/cancel a timer (with optional ordinal)
            active_timers = [t for t in self.timer_app.timers if t.remaining_seconds > 0]
            timer_index = params.get("timer_index")

            if not active_timers:
                print(f"  [Voice: No active timer to cancel]")
                self.tts.speak_async("No active timer to cancel.")
            elif len(active_timers) == 1:
                # Only one timer, just cancel it
                self.timer_app.timers.remove(active_timers[0])
                print(f"  [Voice: Cancelled timer]")
                self.tts.speak_async("Timer cancelled.")
            elif timer_index is not None:
                # Specific timer requested
                if timer_index < len(active_timers):
                    self.timer_app.timers.remove(active_timers[timer_index])
                    ordinal = ["first", "second", "third", "fourth", "fifth"][timer_index]
                    print(f"  [Voice: Cancelled {ordinal} timer]")
                    self.tts.speak_async(f"{ordinal.capitalize()} timer cancelled.")
                else:
                    print(f"  [Voice: Timer {timer_index + 1} doesn't exist]")
                    self.tts.speak_async(f"You only have {len(active_timers)} timers.")
            else:
                # Multiple timers, no ordinal specified - ask for clarification
                ordinal_options = self._ordinal_list(len(active_timers))
                print(f"  [Voice: Multiple timers - need ordinal]")
                self.tts.speak_async(f"You have {len(active_timers)} timers. Say cancel the {ordinal_options} timer.")

            self._change_state(AppState.TIMERS)
            self._schedule_update()

        elif intent == "timer_stop_all":
            # Cancel all timers
            count = len(self.timer_app.timers)
            self.timer_app.timers.clear()
            print(f"  [Voice: Cancelled all {count} timers]")
            self.tts.speak_async(f"Cancelled all {count} timers." if count else "No timers to cancel.")
            self._change_state(AppState.TIMERS)
            self._schedule_update()

        elif intent == "timer_count":
            # Report how many timers
            count = len([t for t in self.timer_app.timers if t.remaining_seconds > 0])
            if count == 0:
                self.tts.speak_async("You have no active timers.")
            elif count == 1:
                self.tts.speak_async("You have one active timer.")
            else:
                self.tts.speak_async(f"You have {count} active timers.")
            print(f"  [Voice: Timer count - {count}]")

        elif intent == "timer_status":
            # Report timer status via voice (with optional ordinal)
            active_timers = [t for t in self.timer_app.timers if t.remaining_seconds > 0]
            timer_index = params.get("timer_index")

            if not active_timers:
                self.tts.speak_async("No active timers.")
                print(f"  [Voice: No active timers]")
            else:
                # Pick which timer to report
                if timer_index is not None and timer_index < len(active_timers):
                    timer = active_timers[timer_index]
                    ordinal = ["first", "second", "third", "fourth", "fifth"][timer_index]
                    prefix = f"The {ordinal} timer has "
                else:
                    timer = active_timers[-1]  # Most recent
                    prefix = "" if len(active_timers) == 1 else "The last timer has "

                mins = timer.remaining_seconds // 60
                secs = timer.remaining_seconds % 60
                if mins > 0:
                    self.tts.speak_async(f"{prefix}{mins} minutes and {secs} seconds remaining.")
                else:
                    self.tts.speak_async(f"{prefix}{secs} seconds remaining.")
                print(f"  [Voice: Timer status - {mins}m {secs}s remaining]")

        elif intent == "timer_add_time":
            # Add time to a timer (with optional ordinal)
            minutes = params.get("minutes", 5)
            timer_index = params.get("timer_index")
            active_timers = [t for t in self.timer_app.timers if t.remaining_seconds > 0]

            if not active_timers:
                print(f"  [Voice: No timer to add time to]")
                self.tts.speak_async("No active timer.")
            else:
                # Pick which timer
                if timer_index is not None and timer_index < len(active_timers):
                    timer = active_timers[timer_index]
                else:
                    timer = active_timers[-1]  # Most recent

                timer.remaining_seconds += minutes * 60
                timer.total_seconds += minutes * 60
                # Grammar: "1 minute" vs "2 minutes"
                unit = "minute" if minutes == 1 else "minutes"
                print(f"  [Voice: Added {minutes} {unit} to timer]")
                self.tts.speak_async(f"Added {minutes} {unit}.")
                self._change_state(AppState.TIMERS)
                self._schedule_update()

        elif intent == "timer_pause":
            # Pause a timer (with optional ordinal)
            timer_index = params.get("timer_index")
            running_timers = [t for t in self.timer_app.timers if t.remaining_seconds > 0 and not t.paused]

            if not running_timers:
                paused_count = len([t for t in self.timer_app.timers if t.paused])
                if paused_count > 0:
                    self.tts.speak_async(f"All timers are already paused.")
                else:
                    self.tts.speak_async("No active timer to pause.")
                print(f"  [Voice: No running timer to pause]")
            elif len(running_timers) == 1:
                running_timers[0].paused = True
                print(f"  [Voice: Paused timer]")
                self.tts.speak_async("Timer paused.")
                self._change_state(AppState.TIMERS)
                self._schedule_update()
            elif timer_index is not None and timer_index < len(running_timers):
                running_timers[timer_index].paused = True
                ordinal = ["first", "second", "third", "fourth", "fifth"][timer_index]
                print(f"  [Voice: Paused {ordinal} timer]")
                self.tts.speak_async(f"{ordinal.capitalize()} timer paused.")
                self._change_state(AppState.TIMERS)
                self._schedule_update()
            else:
                ordinal_options = self._ordinal_list(len(running_timers))
                print(f"  [Voice: Multiple running timers - need ordinal]")
                self.tts.speak_async(f"You have {len(running_timers)} running timers. Say pause the {ordinal_options} timer.")

        elif intent == "timer_resume":
            # Resume a paused timer (with optional ordinal)
            timer_index = params.get("timer_index")
            paused_timers = [t for t in self.timer_app.timers if t.paused and t.remaining_seconds > 0]

            if not paused_timers:
                self.tts.speak_async("No paused timer to resume.")
                print(f"  [Voice: No paused timer to resume]")
            elif len(paused_timers) == 1:
                paused_timers[0].paused = False
                print(f"  [Voice: Resumed timer]")
                self.tts.speak_async("Timer resumed.")
                self._change_state(AppState.TIMERS)
                self._schedule_update()
            elif timer_index is not None and timer_index < len(paused_timers):
                paused_timers[timer_index].paused = False
                ordinal = ["first", "second", "third", "fourth", "fifth"][timer_index]
                print(f"  [Voice: Resumed {ordinal} timer]")
                self.tts.speak_async(f"{ordinal.capitalize()} timer resumed.")
                self._change_state(AppState.TIMERS)
                self._schedule_update()
            else:
                ordinal_options = self._ordinal_list(len(paused_timers))
                print(f"  [Voice: Multiple paused timers - need ordinal]")
                self.tts.speak_async(f"You have {len(paused_timers)} paused timers. Say resume the {ordinal_options} timer.")

        # === RECIPE COMMANDS ===
        elif intent == "recipe_show":
            recipe_name = params.get("name", "")
            if recipe_name:
                print(f"  [Voice: Searching for recipe '{recipe_name}']")
                if self._search_and_open_recipe(recipe_name):
                    self._change_state(AppState.RECIPES)
                else:
                    print(f"  [Voice: Recipe '{recipe_name}' not found]")
                    self.tts.speak_async(f"Sorry, I couldn't find a recipe for {recipe_name}.")
            self._schedule_update()

        elif intent == "recipe_ingredients":
            # Read out ingredients for a recipe
            recipe_name = params.get("name", "")
            if recipe_name:
                print(f"  [Voice: Looking up ingredients for '{recipe_name}']")
                ingredients = self._get_recipe_ingredients(recipe_name)
                if ingredients:
                    # Speak the ingredients
                    intro = f"For {recipe_name}, you'll need: "
                    ingredient_list = ", ".join(ingredients[:8])  # Limit to first 8
                    if len(ingredients) > 8:
                        ingredient_list += f", and {len(ingredients) - 8} more items"
                    self.tts.speak_async(intro + ingredient_list)
                else:
                    self.tts.speak_async(f"Sorry, I couldn't find that recipe.")

        elif intent == "category_browse":
            # Show recipes in a category
            category = params.get("category")
            if category:
                print(f"  [Voice: Browsing category '{category}']")
                self.recipe_app.current_category = category
                self.recipe_app._load_recipes(category)
                from apps.recipes import RecipeState
                self.recipe_app.state = RecipeState.RECIPE_LIST
                self.recipe_app.selected_index = 0
                self._change_state(AppState.RECIPES)
                self._schedule_update()
            else:
                raw = params.get("raw", "")
                print(f"  [Voice: Unknown category '{raw}']")
                self.tts.speak_async(f"I don't have a category called {raw}.")

        elif intent == "recipe_cook_time":
            # Get cook time for a recipe
            use_current = params.get("use_current", False)
            recipe_name = params.get("name", "")

            recipe = None
            if use_current:
                recipe = self._get_context_recipe()
                if recipe:
                    recipe_name = recipe.name
            elif recipe_name:
                recipe = self._find_recipe(recipe_name)

            if recipe:
                cook_time = recipe.metadata.get("cook_time") or recipe.metadata.get("time_required")
                if cook_time:
                    print(f"  [Voice: Cook time for {recipe_name}: {cook_time}]")
                    self.tts.speak_async(f"{recipe_name} takes {cook_time}.")
                else:
                    print(f"  [Voice: No cook time for {recipe_name}]")
                    self.tts.speak_async(f"I don't have a cook time for {recipe_name}.")
            else:
                if use_current:
                    self.tts.speak_async("I'm not sure which recipe you mean. Try saying the recipe name.")
                else:
                    self.tts.speak_async(f"Sorry, I couldn't find a recipe for {recipe_name}.")
                print(f"  [Voice: Recipe not found]")

        elif intent == "recipe_oven_time":
            # Get oven/baking time from recipe instructions
            use_current = params.get("use_current", False)
            recipe_name = params.get("name", "")

            recipe = None
            if use_current:
                recipe = self._get_context_recipe()
                if recipe:
                    recipe_name = recipe.name
            elif recipe_name:
                recipe = self._find_recipe(recipe_name)

            if recipe:
                # Parse instructions to find baking/cooking times
                # Look for patterns like "bake for 20 minutes", "Bake...for ~{12-14%minutes}"
                import re
                oven_time = None
                for section in recipe.sections:
                    for para in section.paragraphs:
                        # Look for "bake/cook/roast" followed by time (with possible text in between)
                        if re.search(r'\b(?:bake|roast)\b', para, re.I):
                            # Found a baking instruction, now find the time
                            # Match cooklang format ~{12-14%minutes} or (12 minutes) or just "12 minutes"
                            time_match = re.search(r'~?\{?(\d+(?:[-–]\d+)?)\s*%?\s*(minutes?|mins?|hours?|hrs?)\}?', para, re.I)
                            if time_match:
                                time_val = time_match.group(1).replace('-', ' to ').replace('–', ' to ')
                                time_unit = time_match.group(2).lower()
                                if time_unit.startswith('h'):
                                    time_unit = 'hour' if time_val == '1' else 'hours'
                                else:
                                    time_unit = 'minute' if time_val == '1' else 'minutes'
                                oven_time = f"{time_val} {time_unit}"
                                break
                    if oven_time:
                        break

                if oven_time:
                    print(f"  [Voice: Oven time for {recipe_name}: {oven_time}]")
                    self.tts.speak_async(f"Bake for {oven_time}.")
                else:
                    print(f"  [Voice: No oven time found for {recipe_name}]")
                    self.tts.speak_async(f"I couldn't find a baking time for {recipe_name}.")
            else:
                if use_current:
                    self.tts.speak_async("I'm not sure which recipe you mean. Try saying the recipe name.")
                else:
                    self.tts.speak_async(f"Sorry, I couldn't find a recipe for {recipe_name}.")
                print(f"  [Voice: Recipe not found]")

        elif intent == "recipe_temperature":
            # Get oven temperature for a recipe
            use_current = params.get("use_current", False)
            recipe_name = params.get("name", "")

            recipe = None
            if use_current:
                recipe = self._get_context_recipe()
                if recipe:
                    recipe_name = recipe.name
            elif recipe_name:
                recipe = self._find_recipe(recipe_name)

            if recipe:
                # Look for temperature in metadata or parse from first instruction
                temp = recipe.metadata.get("temperature") or recipe.metadata.get("oven_temp")
                if not temp:
                    # Try to find temperature in instructions (e.g., "350°F" or "180°C")
                    import re
                    for section in recipe.sections:
                        for para in section.paragraphs:
                            match = re.search(r'(\d{3})\s*[°˚]?\s*([FCfc])', para)
                            if match:
                                temp = f"{match.group(1)}°{match.group(2).upper()}"
                                break
                        if temp:
                            break

                if temp:
                    print(f"  [Voice: Oven temp for {recipe_name}: {temp}]")
                    # Clean temp for speech: "350°F" -> "350 degrees"
                    temp_speech = re.sub(r'[°˚]\s*[FCfc]?', ' degrees', temp).strip()
                    self.tts.speak_async(f"Set the oven to {temp_speech}.")
                else:
                    print(f"  [Voice: No temperature for {recipe_name}]")
                    self.tts.speak_async(f"I couldn't find an oven temperature for {recipe_name}.")
            else:
                if use_current:
                    self.tts.speak_async("I'm not sure which recipe you mean. Try saying the recipe name.")
                else:
                    self.tts.speak_async(f"Sorry, I couldn't find a recipe for {recipe_name}.")
                print(f"  [Voice: Recipe not found]")

        # === NAVIGATION COMMANDS ===
        elif intent == "go_home":
            print(f"  [Voice: Going home]")
            self._change_state(AppState.HOME)
            self._schedule_update()

        elif intent == "go_back":
            print(f"  [Voice: Going back]")
            # Simulate back button behavior
            if self.state == AppState.RECIPES:
                if not self.recipe_app.back():
                    self._change_state(AppState.MENU)
            elif self.state == AppState.TIMERS:
                if not self.timer_app.back():
                    self._change_state(AppState.MENU)
            elif self.state == AppState.MUSIC:
                self._change_state(AppState.MENU)
            elif self.state == AppState.MENU:
                self._change_state(AppState.HOME)
            self._schedule_update()

        elif intent == "open_menu":
            print(f"  [Voice: Opening menu]")
            self._change_state(AppState.MENU)
            self._schedule_update()

        elif intent == "open_timers":
            print(f"  [Voice: Opening timers]")
            self._change_state(AppState.TIMERS)
            self._schedule_update()

        elif intent == "open_recipes":
            print(f"  [Voice: Opening recipes]")
            self._change_state(AppState.RECIPES)
            self._schedule_update()

        elif intent == "open_music":
            print(f"  [Voice: Opening music]")
            self._change_state(AppState.MUSIC)
            self._schedule_update()

        # === DISPLAY COMMANDS ===
        elif intent == "refresh_screen":
            print(f"  [Voice: Refreshing screen]")
            if self.state == AppState.HOME:
                # On home screen, simulate re-entering home (reload wallpaper + clear-first)
                self.home_app.reload_wallpaper()
                self._needs_clear_first = True
                self._schedule_update()
            else:
                # On other screens, do a deep refresh
                self._do_deep_refresh()

        # === WEATHER COMMANDS ===
        elif intent == "weather":
            print(f"  [Voice: Fetching weather]")
            weather = get_weather()
            if weather:
                speech = format_weather_speech(weather)
                print(f"  [Weather: {speech}]")
                self.tts.speak_async(speech)
            else:
                self.tts.speak_async("Sorry, I couldn't get the weather right now.")

        elif intent == "temperature":
            print(f"  [Voice: Fetching temperature]")
            weather = get_weather()
            if weather:
                speech = format_temperature_speech(weather)
                print(f"  [Temperature: {speech}]")
                self.tts.speak_async(speech)
            else:
                self.tts.speak_async("Sorry, I couldn't get the temperature right now.")

        # === TIME COMMANDS ===
        elif intent == "time":
            now = datetime.now()
            hour = now.hour
            minute = now.minute
            am_pm = "AM" if hour < 12 else "PM"
            hour_12 = hour % 12 or 12
            if minute == 0:
                speech = f"It's {hour_12} {am_pm}."
            elif minute < 10:
                # "5:06" -> "five oh six" not "five zero six"
                speech = f"It's {hour_12} oh {minute} {am_pm}."
            else:
                speech = f"It's {hour_12} {minute} {am_pm}."
            print(f"  [Voice: Time - {speech}]")
            self.tts.speak_async(speech)

        elif intent == "date":
            now = datetime.now()
            speech = now.strftime("Today is %A, %B %d.")
            print(f"  [Voice: Date - {speech}]")
            self.tts.speak_async(speech)

        # === SPOTIFY COMMANDS ===
        elif intent == "spotify_play":
            query = params.get("query", "")
            if not self.spotify.available:
                print(f"  [Voice: Play '{query}' - Spotify not configured]")
                self.tts.speak_async("Spotify isn't set up yet.")
            elif query:
                print(f"  [Voice: Searching Spotify for '{query}']")
                self._change_state(AppState.MUSIC)
                self._schedule_update()
                # Run search in background so TTS doesn't block
                def _search_and_play():
                    track = self.spotify.play_search(query)
                    if track:
                        self.tts.speak_async(f"Playing {track}.")
                    else:
                        self.tts.speak_async(f"Couldn't find {query} on Spotify.")
                threading.Thread(target=_search_and_play, daemon=True).start()
            else:
                print(f"  [Voice: Resume playback]")
                self.spotify.resume()
                self._change_state(AppState.MUSIC)
                self._schedule_update()
                self.tts.speak_async("Resuming.")

        elif intent == "spotify_pause":
            if not self.spotify.available:
                print(f"  [Voice: Pause - Spotify not configured]")
                self.tts.speak_async("Spotify isn't set up yet.")
            elif self.music_app.is_playing:
                print(f"  [Voice: Pausing music]")
                self.spotify.pause()
                self.tts.speak_async("Paused.")
            else:
                print(f"  [Voice: Resuming music]")
                self.spotify.resume()
                self.tts.speak_async("Resuming.")

        elif intent == "spotify_skip":
            if not self.spotify.available:
                print(f"  [Voice: Skip - Spotify not configured]")
                self.tts.speak_async("Spotify isn't set up yet.")
            else:
                print(f"  [Voice: Skipping track]")
                self.spotify.next_track()
                self.tts.speak_async("Skipping.")

    def _get_context_recipe(self):
        """
        Get the contextually valid 'current recipe'.

        Returns a recipe only if:
        1. We're currently viewing a recipe (AppState.RECIPES + RecipeState.RECIPE_VIEW), OR
        2. There's exactly one active timer with a recipe label

        Returns None otherwise.
        """
        from apps.recipes import RecipeState

        # Case 1: Currently viewing a recipe
        if (self.state == AppState.RECIPES and
            self.recipe_app.state == RecipeState.RECIPE_VIEW and
            self.recipe_app.current_recipe):
            return self.recipe_app.current_recipe

        # Case 2: Single active timer with a recipe label
        active_timers = [t for t in self.timer_app.timers
                        if not t.paused and t.remaining_seconds > 0 and t.label]

        if len(active_timers) == 1:
            # Find the recipe by the timer's label
            timer_label = active_timers[0].label
            recipe = self._find_recipe(timer_label)
            if recipe:
                return recipe

        return None

    def _search_and_open_recipe(self, search_term: str) -> bool:
        """Search for a recipe by name and open it if found. Uses word-by-word matching."""
        from config import RECIPE_DIR

        # Normalize search term into words
        search_words = set(search_term.lower().replace("-", " ").replace("_", " ").split())
        # Remove very common words that don't help matching
        search_words -= {"the", "a", "an", "and", "or", "with", "in", "on", "for"}

        best_match = None
        best_score = 0

        # Search through all categories and recipes
        if os.path.exists(RECIPE_DIR):
            for category in os.listdir(RECIPE_DIR):
                category_path = os.path.join(RECIPE_DIR, category)
                if os.path.isdir(category_path):
                    for recipe_file in os.listdir(category_path):
                        if recipe_file.endswith('.cook'):
                            recipe_name = recipe_file[:-5]  # Remove .cook
                            # Normalize recipe name into words
                            recipe_words = set(recipe_name.lower().replace("-", " ").replace("_", " ").replace("(", " ").replace(")", " ").split())
                            recipe_words -= {"the", "a", "an", "and", "or", "with", "in", "on", "for"}

                            # Score: count matching words
                            matching = search_words & recipe_words
                            score = len(matching)

                            # Bonus for exact substring match
                            search_joined = "".join(sorted(search_words))
                            recipe_joined = "".join(sorted(recipe_words))
                            if search_joined in recipe_joined or recipe_joined in search_joined:
                                score += 2

                            if score > best_score:
                                best_score = score
                                best_match = (category, recipe_name)

        # Require at least 1 matching word
        if best_match and best_score >= 1:
            category, recipe_name = best_match
            self.recipe_app.current_category = category
            self.recipe_app._load_recipes(category)
            self.recipe_app.current_recipe = self.recipe_app._load_recipe(category, recipe_name)
            if self.recipe_app.current_recipe:
                from apps.recipes import RecipeState
                self.recipe_app.state = RecipeState.RECIPE_VIEW
                self.recipe_app.scroll_offset = 0
                print(f"  [Voice: Found recipe '{recipe_name}' in '{category}' (score: {best_score})]")
                return True
        return False

    def _get_recipe_ingredients(self, search_term: str) -> list:
        """Search for a recipe and return its ingredients list."""
        from config import RECIPE_DIR

        # Normalize search term into words
        search_words = set(search_term.lower().replace("-", " ").replace("_", " ").split())
        search_words -= {"the", "a", "an", "and", "or", "with", "in", "on", "for"}

        best_match = None
        best_score = 0

        if os.path.exists(RECIPE_DIR):
            for category in os.listdir(RECIPE_DIR):
                category_path = os.path.join(RECIPE_DIR, category)
                if os.path.isdir(category_path):
                    for recipe_file in os.listdir(category_path):
                        if recipe_file.endswith('.cook'):
                            recipe_name = recipe_file[:-5]
                            recipe_words = set(recipe_name.lower().replace("-", " ").replace("_", " ").replace("(", " ").replace(")", " ").split())
                            recipe_words -= {"the", "a", "an", "and", "or", "with", "in", "on", "for"}

                            matching = search_words & recipe_words
                            score = len(matching)

                            if score > best_score:
                                best_score = score
                                best_match = (category, recipe_name)

        if best_match and best_score >= 1:
            category, recipe_name = best_match
            recipe = self.recipe_app._load_recipe(category, recipe_name)
            if recipe and recipe.ingredients:
                # Return just the ingredient names (not quantities)
                return [ing.name for ing in recipe.ingredients]
        return []

    def _find_recipe(self, search_term: str):
        """Search for a recipe and return the Recipe object."""
        from config import RECIPE_DIR

        # Normalize search term into words
        search_words = set(search_term.lower().replace("-", " ").replace("_", " ").split())
        search_words -= {"the", "a", "an", "and", "or", "with", "in", "on", "for"}

        best_match = None
        best_score = 0

        if os.path.exists(RECIPE_DIR):
            for category in os.listdir(RECIPE_DIR):
                category_path = os.path.join(RECIPE_DIR, category)
                if os.path.isdir(category_path):
                    for recipe_file in os.listdir(category_path):
                        if recipe_file.endswith('.cook'):
                            recipe_name = recipe_file[:-5]
                            recipe_words = set(recipe_name.lower().replace("-", " ").replace("_", " ").replace("(", " ").replace(")", " ").split())
                            recipe_words -= {"the", "a", "an", "and", "or", "with", "in", "on", "for"}

                            matching = search_words & recipe_words
                            score = len(matching)

                            if score > best_score:
                                best_score = score
                                best_match = (category, recipe_name)

        if best_match and best_score >= 1:
            category, recipe_name = best_match
            return self.recipe_app._load_recipe(category, recipe_name)
        return None

    # ==================== State Management ====================

    def _change_state(self, new_state: AppState):
        """Change to a new app state."""
        old_state = self.state
        self.previous_state = self.state
        self.state = new_state
        self._last_state_change = time.time()
        self._record_activity()
        print(f"  [State: {self.previous_state.value} -> {new_state.value}]")

        # Clear-first needed when transitioning between screens with different backgrounds
        # This does a white partial refresh before content, avoiding full blink
        # - FROM home: dark wallpaper to white menu/apps
        # - TO home: white screens to dark wallpaper
        # - TO music: clear text ghosting
        if old_state == AppState.HOME or new_state == AppState.HOME or new_state == AppState.MUSIC:
            self._needs_clear_first = True

        # Load new wallpaper when entering home from a different screen
        if new_state == AppState.HOME and old_state != AppState.HOME:
            self.home_app.reload_wallpaper()

        # Reset music timeout and entry state when entering music app
        if new_state == AppState.MUSIC:
            # Sync _last_known_track so _on_music_update() doesn't see a "new track"
            # on entry and snap to NOW_PLAYING via on_new_track()
            self._last_known_track = self.music_app.track_name
            self.music_app.reset_to_entry_state()
            self.last_music_playing_time = time.time()

        # Reload recipes when entering recipes app (picks up newly added recipes)
        if new_state == AppState.RECIPES:
            self.recipe_app.reload_recipes()

    def _go_back(self) -> bool:
        """
        Go back one level. Returns True if handled internally by app,
        False if should go back to parent (menu).
        """
        if self.state == AppState.RECIPES:
            return self.recipe_app.back()
        elif self.state == AppState.TIMERS:
            return self.timer_app.back()
        elif self.state == AppState.MUSIC:
            return self.music_app.back()
        return False

    # ==================== Timer Alarm ====================

    def _on_timer_alarm(self, timer):
        """Handle timer completion - show alarm screen."""
        self.timer_alarm_previous_state = self.state
        self.timer_alarm_active = True
        self.timer_app.alarm_add_minutes = 0  # Reset add-time counter

        # Need clear-first if coming from dark home screen
        if self.state == AppState.HOME:
            self._needs_clear_first = True

        # Call original handler for alarm sound
        self._original_timer_alarm_handler(timer)

        self._schedule_update()
        print(f"  [Timer alarm! Will return to {self.timer_alarm_previous_state.value}]")

    def _dismiss_timer_alarm(self):
        """Dismiss timer alarm and return to previous state."""
        add_minutes = self.timer_app.alarm_add_minutes
        self.timer_app.stop_alarm(add_minutes=add_minutes)
        self.timer_alarm_active = False

        # Return to previous state
        if self.timer_alarm_previous_state:
            # Need clear-first when returning to home (white alarm -> dark wallpaper)
            if self.timer_alarm_previous_state == AppState.HOME:
                self._needs_clear_first = True
            self.state = self.timer_alarm_previous_state
            self.timer_alarm_previous_state = None
            if add_minutes > 0:
                print(f"  [Timer dismissed, added {add_minutes} min, returning to {self.state.value}]")
            else:
                print(f"  [Timer dismissed, returning to {self.state.value}]")

    # ==================== Volume Overlay ====================

    def _show_volume_overlay(self, volume: int):
        """Show volume overlay temporarily."""
        self.volume_overlay_active = True
        self.volume_overlay_hide_time = time.time() + self.VOLUME_DISPLAY_TIME
        # Always schedule update to show new volume level
        self._schedule_update()

    def _draw_volume_on_framebuffer(self):
        """Draw volume overlay with 7 dots that grow based on volume level."""
        # Convert 0-100 to 1-10
        volume_level = max(1, min(10, (self.audio.volume + 9) // 10))

        # Use different position for home screen (top) vs other screens (bottom)
        # Home screen has time overlay at bottom-left, so put volume at top
        overlay_width = 400
        overlay_height = 90
        overlay_x = (DISPLAY_WIDTH - overlay_width) // 2  # Centered horizontally

        if self.state == AppState.HOME:
            overlay_y = 10  # Top of screen
        else:
            overlay_y = DISPLAY_HEIGHT - 100  # Bottom of screen

        draw = ImageDraw.Draw(self.renderer.framebuffer)

        # Draw white background
        draw.rectangle(
            [overlay_x, overlay_y, overlay_x + overlay_width - 1, overlay_y + overlay_height - 1],
            fill=1
        )

        # Draw box
        draw.rounded_rectangle(
            [overlay_x, overlay_y, overlay_x + overlay_width - 1, overlay_y + overlay_height - 1],
            radius=15, outline=0, width=3
        )

        # Load font for number
        try:
            number_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        except:
            number_font = ImageFont.load_default()

        # Volume number at top
        vol_str = str(volume_level)
        vol_bbox = draw.textbbox((0, 0), vol_str, font=number_font)
        vol_x = overlay_x + (overlay_width - (vol_bbox[2] - vol_bbox[0])) // 2
        draw.text((vol_x, overlay_y + 8), vol_str, font=number_font, fill=0)

        # Draw 7 dots at bottom - sizes based on volume
        # Dot sizes range from 6 (min) to 22 (max)
        num_dots = 7
        dot_spacing = 45
        dots_total_width = (num_dots - 1) * dot_spacing
        start_x = overlay_x + (overlay_width - dots_total_width) // 2
        dot_y = overlay_y + 58  # Center y for dots

        # Calculate which dots should be "active" (larger) based on volume
        # Volume 1: only middle dot (index 3) is large
        # Volume 10: all dots are large
        # Dots grow outward from center as volume increases
        center_idx = 3  # Middle of 7 dots (0-indexed)

        for i in range(num_dots):
            x = start_x + i * dot_spacing

            # Distance from center (0 for middle, 1 for adjacent, 2, 3 for edges)
            dist_from_center = abs(i - center_idx)

            # At volume 1, only center is big (dist 0)
            # At volume 4, dist 0,1 are big
            # At volume 7, dist 0,1,2 are big
            # At volume 10, all are big (dist 0,1,2,3)
            # threshold = (volume_level - 1) / 3  -> 0 at vol 1, 1 at vol 4, 2 at vol 7, 3 at vol 10
            threshold = (volume_level - 1) / 3.0

            if dist_from_center <= threshold:
                # Fully active - large dot
                radius = 11
            elif dist_from_center <= threshold + 1:
                # Partially active - medium dot (transitioning)
                # Interpolate size based on how close to threshold
                frac = threshold - (dist_from_center - 1)  # 0 to 1
                radius = int(6 + frac * 5)  # 6 to 11
            else:
                # Inactive - small dot
                radius = 6

            # Draw filled circle
            draw.ellipse(
                [x - radius, dot_y - radius, x + radius, dot_y + radius],
                fill=0
            )

    # ==================== Voice Status Overlay ====================

    def _load_voice_icons(self) -> dict:
        """Load voice status overlay icons, cropped to remove whitespace border."""
        icons = {}
        icon_dir = os.path.join(ASSETS_DIR, "app-icons")
        icon_size = 300

        for name in ["listening-icon", "thinking-icon", "talking-open-icon", "talking-closed-icon", "confused-icon"]:
            path = os.path.join(icon_dir, f"{name}.png")
            if os.path.exists(path):
                img = Image.open(path)
                # Crop 15% from all sides to remove whitespace border
                w, h = img.size
                margin_x = int(w * 0.15)
                margin_y = int(h * 0.15)
                img = img.crop((margin_x, margin_y, w - margin_x, h - margin_y))
                img.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
                icons[name] = img.convert('L').convert('1')
            else:
                print(f"  [Warning: Voice icon not found: {path}]")
                icons[name] = None
        return icons

    def _on_voice_status(self, status: str):
        """Called from voice thread when voice interaction state changes."""
        print(f"  [Voice status: {status}]")

        if status == "listening":
            self._voice_overlay_state = VoiceOverlayState.LISTENING
            # Non-blocking: refresh in background so voice thread starts recording immediately
            threading.Thread(target=self._draw_voice_overlay_immediate, daemon=True).start()
        elif status == "thinking":
            self._voice_overlay_state = VoiceOverlayState.THINKING
            self._draw_voice_overlay_immediate()
        elif status == "command_done":
            # Command handler finished. If TTS was triggered, overlay will
            # transition to TALKING via on_speaking_changed. If not, clear
            # the overlay after a brief grace period.
            def _check_idle():
                time.sleep(0.5)
                if self._voice_overlay_state in (VoiceOverlayState.THINKING, VoiceOverlayState.LISTENING):
                    self._voice_overlay_state = VoiceOverlayState.IDLE
                    self._schedule_update()
            threading.Thread(target=_check_idle, daemon=True).start()
        elif status == "confused":
            self._voice_overlay_state = VoiceOverlayState.CONFUSED
            self._draw_voice_overlay_immediate()
            # Show confused icon for 2 seconds then clear
            def _clear_confused():
                time.sleep(2)
                if self._voice_overlay_state == VoiceOverlayState.CONFUSED:
                    self._voice_overlay_state = VoiceOverlayState.IDLE
                    self._schedule_update()
            threading.Thread(target=_clear_confused, daemon=True).start()
        elif status == "idle":
            self._stop_talking_animation()
            self._voice_overlay_state = VoiceOverlayState.IDLE
            self._schedule_update()

    def _on_tts_speaking_changed(self, is_speaking: bool):
        """Called from TTS thread when speech starts/stops."""
        if is_speaking:
            self._voice_overlay_state = VoiceOverlayState.TALKING
            self._talking_mouth_open = False  # Start closed
            self._draw_voice_overlay_immediate()
            self._start_talking_animation()
        else:
            self._stop_talking_animation()
            self._voice_overlay_state = VoiceOverlayState.IDLE
            self._schedule_update()

    def _start_talking_animation(self):
        """Start the talking mouth animation loop."""
        self._stop_talking_animation()
        # Start with a brief pause then first open burst
        self._talking_animation_timer = threading.Timer(0.3, self._talking_open_burst)
        self._talking_animation_timer.daemon = True
        self._talking_animation_timer.start()

    def _talking_open_burst(self):
        """Open mouth briefly, then close and schedule next burst."""
        if self._voice_overlay_state != VoiceOverlayState.TALKING:
            return
        # Open mouth
        self._talking_mouth_open = True
        self._draw_voice_overlay_immediate()
        # Close after a short burst (150-200ms)
        import random
        close_delay = random.uniform(0.15, 0.25)
        self._talking_animation_timer = threading.Timer(close_delay, self._talking_close_and_wait)
        self._talking_animation_timer.daemon = True
        self._talking_animation_timer.start()

    def _talking_close_and_wait(self):
        """Close mouth and wait before next open burst."""
        if self._voice_overlay_state != VoiceOverlayState.TALKING:
            return
        # Close mouth
        self._talking_mouth_open = False
        self._draw_voice_overlay_immediate()
        # Wait before next burst (400-700ms)
        import random
        wait_delay = random.uniform(0.4, 0.7)
        self._talking_animation_timer = threading.Timer(wait_delay, self._talking_open_burst)
        self._talking_animation_timer.daemon = True
        self._talking_animation_timer.start()

    def _stop_talking_animation(self):
        """Stop the talking mouth animation."""
        if self._talking_animation_timer:
            self._talking_animation_timer.cancel()
            self._talking_animation_timer = None

    def _draw_voice_overlay_immediate(self):
        """Draw voice overlay icon and do an immediate partial refresh."""
        with self._lock:
            self._draw_voice_overlay_on_framebuffer()
            # Partial refresh just the overlay region
            if not self.renderer.in_partial_mode:
                self.renderer.init_partial()
            buf = self.renderer.epd.getbuffer(self.renderer.framebuffer)
            self.renderer.epd.display_Partial(buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)

    def _draw_voice_overlay_on_framebuffer(self):
        """Draw the voice status icon overlay centered on screen."""
        if self._voice_overlay_state == VoiceOverlayState.LISTENING:
            icon = self._voice_overlay_icons.get("listening-icon")
        elif self._voice_overlay_state == VoiceOverlayState.THINKING:
            icon = self._voice_overlay_icons.get("thinking-icon")
        elif self._voice_overlay_state == VoiceOverlayState.TALKING:
            if self._talking_mouth_open:
                icon = self._voice_overlay_icons.get("talking-open-icon")
            else:
                icon = self._voice_overlay_icons.get("talking-closed-icon")
        elif self._voice_overlay_state == VoiceOverlayState.CONFUSED:
            icon = self._voice_overlay_icons.get("confused-icon")
        else:
            return

        if not icon:
            return

        draw = ImageDraw.Draw(self.renderer.framebuffer)

        # Overlay box dimensions
        padding = 10
        box_w = icon.width + padding * 2
        box_h = icon.height + padding * 2
        box_x = (DISPLAY_WIDTH - box_w) // 2
        box_y = (DISPLAY_HEIGHT - box_h) // 2

        # White background with rounded rectangle border
        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            radius=20, fill=1, outline=0, width=3
        )

        # Paste icon centered in box
        icon_x = box_x + padding
        icon_y = box_y + padding
        self.renderer.framebuffer.paste(icon, (icon_x, icon_y))

    # ==================== Callbacks ====================

    def _on_timer_update(self):
        """Callback when timer app needs update."""
        self._schedule_update()

    def _on_music_update(self):
        """Callback when music app needs update (track change or play state)."""
        self._check_spotify_connection()
        # Snap to NOW_PLAYING only when the track name actually changes
        # (not on play/pause or list-load callbacks - those would kick the user out of browse views)
        current_track = self.music_app.track_name
        if self.state == AppState.MUSIC and current_track and current_track != self._last_known_track:
            self.music_app.on_new_track()
        self._last_known_track = current_track
        self._schedule_update()

    def _on_music_progress_update(self):
        """Callback for music progress partial refresh."""
        if self._voice_overlay_state != VoiceOverlayState.IDLE:
            return
        if self._needs_clear_first or self._needs_full_refresh:
            return
        if time.time() < self._full_refresh_cooldown_until:
            return

        if self.state == AppState.MUSIC and self.music_app.music_state == MusicState.NOW_PLAYING:
            # Full progress bar partial refresh on the NOW_PLAYING screen
            with self._lock:
                self.music_app.update_progress()
        elif self._mini_player_visible() and self.music_app.track_name:
            # Rate-limit mini player refresh on non-music screens: every 5 seconds
            self._mini_player_tick += 1
            if self._mini_player_tick >= 5:
                self._mini_player_tick = 0
                self._schedule_update()

    def _check_spotify_connection(self):
        """Check if Spotify just connected and switch to music app."""
        # Only check every second
        now = time.time()
        if now - self._last_spotify_check < 1:
            return
        self._last_spotify_check = now

        # Check if music is now playing
        is_connected = self.music_app.track_name != "" and self.music_app.is_playing

        # If just connected (wasn't playing, now is), switch to music
        if is_connected and not self._last_spotify_connected:
            if self.state != AppState.MUSIC and not self.timer_alarm_active:
                print("  [Spotify connected - switching to Music app]")
                self._change_state(AppState.MUSIC)
                self._schedule_update()

        # Track playing state for pause timeout
        if self.music_app.is_playing:
            self.last_music_playing_time = now

        self._last_spotify_connected = is_connected

    # ==================== Mini Player ====================

    def _mini_player_visible(self) -> bool:
        """Return True when the mini player strip should be shown."""
        if not self.music_app.track_name:
            return False
        if self.state == AppState.HOME:
            return False
        if self.state == AppState.MUSIC and self.music_app.music_state == MusicState.NOW_PLAYING:
            return False
        if self.state == AppState.RECIPES and self.recipe_app.state not in (
                RecipeState.CATEGORIES, RecipeState.RECIPE_LIST):
            return False
        if self.state == AppState.TIMERS and self.timer_app.state != TimerState.LIST:
            return False
        return True

    def _draw_mini_player(self, img: Image.Image, draw: ImageDraw.Draw):
        """Draw a compact now-playing strip at y=430-480."""
        Y_TOP = 430

        draw.line([(0, Y_TOP), (DISPLAY_WIDTH, Y_TOP)], fill=0, width=1)

        # Thumbnail (32x32)
        thumb = self.music_app.get_current_thumbnail(32)
        thumb_x, thumb_y = 5, Y_TOP + 9
        if thumb:
            img.paste(thumb, (thumb_x, thumb_y))
        else:
            draw.rectangle(
                [thumb_x, thumb_y, thumb_x + 32, thumb_y + 32], outline=0, width=1)
            draw.text((thumb_x + 8, thumb_y + 8), "♪", font=self._mini_font, fill=0)

        # Track name and artist
        text_x = 45
        max_text_w = 470

        track = self.music_app.track_name.replace("\n", " ")
        artist = self.music_app.artist_name.replace("\n", ", ")
        track_trunc = self.music_app._truncate_text(track, self._mini_font_bold, max_text_w)
        artist_trunc = self.music_app._truncate_text(artist, self._mini_font, max_text_w)

        draw.text((text_x, Y_TOP + 5), track_trunc, font=self._mini_font_bold, fill=0)
        draw.text((text_x, Y_TOP + 27), artist_trunc, font=self._mini_font, fill=0)

        # Progress bar (right side)
        bar_x = 530
        bar_w = 180
        bar_y = Y_TOP + 20
        bar_h = 6
        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline=0, width=1)
        if self.music_app.duration_ms > 0:
            progress = min(1.0, self.music_app.position_ms / self.music_app.duration_ms)
            fill_w = int(bar_w * progress)
            if fill_w > 2:
                draw.rectangle(
                    [bar_x + 1, bar_y + 1, bar_x + fill_w - 1, bar_y + bar_h - 1], fill=0)

        # Time display
        current_time = self.music_app._format_time(self.music_app.position_ms)
        total_time = self.music_app._format_time(self.music_app.duration_ms)
        time_str = f"{current_time} / {total_time}"
        time_bbox = draw.textbbox((0, 0), time_str, font=self._mini_font)
        time_x = DISPLAY_WIDTH - (time_bbox[2] - time_bbox[0]) - 8
        draw.text((time_x, Y_TOP + 31), time_str, font=self._mini_font, fill=0)

    # ==================== Timeout Handling ====================

    def _check_timeouts(self):
        """Check for auto-return-to-home conditions."""
        now = time.time()
        idle_time = now - self.last_activity_time

        # Don't timeout during timer alarm
        if self.timer_alarm_active:
            return

        # Menu timeout
        if self.state == AppState.MENU and idle_time > self.MENU_TIMEOUT:
            print("  [Menu timeout - returning to home]")
            self._change_state(AppState.HOME)
            self._schedule_update()
            return

        # Music pause timeout
        if self.state == AppState.MUSIC:
            pause_time = now - self.last_music_playing_time
            if not self.music_app.is_playing and pause_time > self.MUSIC_PAUSE_TIMEOUT:
                print("  [Music paused timeout - returning to home]")
                self._change_state(AppState.HOME)
                self._schedule_update()
                return

        # Timers idle timeout (no active timers)
        if self.state == AppState.TIMERS and idle_time > self.TIMERS_IDLE_TIMEOUT:
            active_timers = [t for t in self.timer_app.timers if not t.paused and t.remaining_seconds > 0]
            if not active_timers:
                print("  [Timers idle timeout - returning to home]")
                self._change_state(AppState.HOME)
                self._schedule_update()
                return

    # ==================== Rendering ====================

    def _do_deep_refresh(self):
        """Do a thorough two-stage refresh to fully clear ghosting.

        Stage 1: Full refresh to white (clears all dark pixels completely)
        Stage 2: Full refresh with actual content
        """
        with self._lock:
            # Stage 1: Clear to white with full refresh
            print("  [Deep refresh - stage 1: clear to white]")
            self.renderer.init()
            white_img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
            white_buf = self.renderer.epd.getbuffer(white_img)
            self.renderer.epd.display(white_buf)

            # Stage 2: Render actual content with full refresh
            print("  [Deep refresh - stage 2: render content]")
            self.renderer.init()  # Re-init for second full refresh

            # Render current screen to framebuffer
            mini_showing = self._mini_player_visible()
            if self.timer_alarm_active:
                self.timer_app._render_alarm()
            elif self.state == AppState.HOME:
                self.home_app.render()
            elif self.state == AppState.MENU:
                self.menu_app.mini_player_active = mini_showing
                self.menu_app.render()
            elif self.state == AppState.RECIPES:
                self.recipe_app.mini_player_active = mini_showing
                self.recipe_app.render()
            elif self.state == AppState.TIMERS:
                self.timer_app.mini_player_active = mini_showing
                self.timer_app.render()
            elif self.state == AppState.MUSIC:
                self.music_app.render()
            if mini_showing:
                draw = ImageDraw.Draw(self.renderer.framebuffer)
                self._draw_mini_player(self.renderer.framebuffer, draw)

            # Display the content
            buf = self.renderer.epd.getbuffer(self.renderer.framebuffer)
            self.renderer.epd.display(buf)

            # Reset state
            self.renderer.in_partial_mode = False
            self._needs_full_refresh = False
            self._needs_clear_first = False
            self._partial_refresh_count = 0
            self._full_refresh_cooldown_until = time.time() + 1.0
            print("  [Deep refresh complete]")

    def _render(self):
        """Render the current state to the display."""
        # Use lock to prevent concurrent display operations with progress updates
        with self._lock:
            self._render_internal()

    def _render_internal(self):
        """Internal render - must be called with _lock held."""
        # Clear-first: do a white partial refresh before rendering content
        # This helps clear dark pixels without a full blink
        # Defer clear-first while voice overlay is active to avoid flashing the overlay away
        if self._needs_clear_first and not self._needs_full_refresh:
            if self._voice_overlay_state == VoiceOverlayState.IDLE:
                # Must be in partial mode before calling display_Partial
                if not self.renderer.in_partial_mode:
                    self.renderer.init_partial()
                white_img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
                white_buf = self.renderer.epd.getbuffer(white_img)
                self.renderer.epd.display_Partial(white_buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
                self._needs_clear_first = False
                print("  [Clear refresh]")

        # Timer alarm takes priority
        mini_showing = self._mini_player_visible()
        if self.timer_alarm_active:
            self.timer_app._render_alarm()
        elif self.state == AppState.HOME:
            self.home_app.render()
        elif self.state == AppState.MENU:
            self.menu_app.mini_player_active = mini_showing
            self.menu_app.render()
        elif self.state == AppState.RECIPES:
            self.recipe_app.mini_player_active = mini_showing
            self.recipe_app.render()
        elif self.state == AppState.TIMERS:
            self.timer_app.mini_player_active = mini_showing
            self.timer_app.render()
        elif self.state == AppState.MUSIC:
            self.music_app.render()

        # Draw mini player over the bottom strip on all applicable screens
        if mini_showing and self._voice_overlay_state == VoiceOverlayState.IDLE:
            draw = ImageDraw.Draw(self.renderer.framebuffer)
            self._draw_mini_player(self.renderer.framebuffer, draw)

        # Draw volume overlay on framebuffer if active
        if self.volume_overlay_active:
            self._draw_volume_on_framebuffer()

        # Draw voice status overlay on framebuffer if active
        if self._voice_overlay_state != VoiceOverlayState.IDLE:
            self._draw_voice_overlay_on_framebuffer()

        # Check if full refresh is needed
        self._partial_refresh_count += 1
        do_full = self._needs_full_refresh or self._partial_refresh_count >= self._max_partial_before_full

        if do_full:
            # Full refresh - clears ghosting
            self.renderer.init()  # Switch to full refresh mode
            buf = self.renderer.epd.getbuffer(self.renderer.framebuffer)
            self.renderer.epd.display(buf)
            # Mark not in partial mode - next partial refresh will call init_partial()
            self.renderer.in_partial_mode = False
            self._needs_full_refresh = False
            self._needs_clear_first = False
            self._partial_refresh_count = 0
            self._skip_next_home_update = True
            # Add cooldown before allowing partial refreshes (gives display time to settle)
            self._full_refresh_cooldown_until = time.time() + 1.0
            print("  [Full refresh]")
        else:
            # Check cooldown - don't do partial refresh too soon after full refresh
            if time.time() < self._full_refresh_cooldown_until:
                print("  [Skipping partial - cooldown active]")
                return
            # Partial refresh - ensure we're in partial mode
            if not self.renderer.in_partial_mode:
                self.renderer.init_partial()
            buf = self.renderer.epd.getbuffer(self.renderer.framebuffer)
            self.renderer.epd.display_Partial(buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)

    # ==================== Main Loop ====================

    def run(self):
        """Main application loop."""
        print("\n" + "=" * 50)
        print("SMART DISPLAY RUNNING")
        print("=" * 50)
        print("Controls:")
        print("  - Rotate encoder: Navigate")
        print("  - Press encoder: Select / Back (on back button)")
        print("  - Hold encoder: Go to Home")
        print("  - Volume buttons: Adjust volume")
        print("=" * 50 + "\n")

        # Initial render
        self._render()

        try:
            while True:
                # Check for pending display update
                if self._pending_update.wait(timeout=0.1):
                    self._pending_update.clear()
                    self._render()

                # Check volume overlay timeout
                if self.volume_overlay_active and time.time() > self.volume_overlay_hide_time:
                    self.volume_overlay_active = False
                    self._schedule_update()  # Re-render without overlay

                # Check for timeouts
                self._check_timeouts()

                # Check Spotify connection
                self._check_spotify_connection()

                # Update home screen time (if on home)
                # Skip during voice overlay - the direct refresh would erase the overlay
                if self.state == AppState.HOME and not self.timer_alarm_active and self._voice_overlay_state == VoiceOverlayState.IDLE:
                    if self._skip_next_home_update:
                        self._skip_next_home_update = False
                    elif time.time() < self._full_refresh_cooldown_until:
                        # Still in cooldown period after full refresh - skip
                        pass
                    elif self.home_app.update():
                        # Ensure we're in partial mode before calling display_Partial
                        if not self.renderer.in_partial_mode:
                            self.renderer.init_partial()
                        buf = self.renderer.epd.getbuffer(self.renderer.framebuffer)
                        self.renderer.epd.display_Partial(buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)

        except KeyboardInterrupt:
            print("\n\nShutting down...")

        self.shutdown()

    def shutdown(self):
        """Clean shutdown of all components."""
        print("Cleaning up...")
        self._stop_talking_animation()
        self.voice.stop()
        self.timer_app.shutdown()
        self.music_app.shutdown()
        self.audio.shutdown()
        self.renderer.clear()
        print("Goodbye!")


def main():
    controller = MainController()
    controller.run()


if __name__ == "__main__":
    main()

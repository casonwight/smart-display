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
from audio.voice import VoiceController
from audio.tts import create_tts
from audio.weather import get_weather, format_weather_speech, format_temperature_speech
from apps.home import HomeApp
from apps.menu import MenuApp, MenuItem
from apps.recipes import RecipeApp
from apps.timers import TimerApp, TimerState
from apps.music import MusicApp, STATE_FILE as SPOTIFY_STATE_FILE
from config import (
    ENCODER_PIN_A, ENCODER_PIN_B, ENCODER_PIN_SW,
    BUTTON_UP_PIN, BUTTON_DOWN_PIN,
    DISPLAY_WIDTH, DISPLAY_HEIGHT,
    VOLUME_STEP
)


class AppState(Enum):
    """Main application states."""
    HOME = "home"
    MENU = "menu"
    RECIPES = "recipes"
    TIMERS = "timers"
    MUSIC = "music"


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

        # Activity tracking for timeouts
        self.last_activity_time = time.time()
        self.last_music_playing_time = time.time()

        # Spotify connection tracking
        self._last_spotify_connected = False
        self._last_spotify_check = 0

        # Debounce
        self._pending_update = threading.Event()
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

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

        # Initialize voice control
        print("  - Voice control...")
        self.voice = VoiceController(
            on_command_callback=self._on_voice_command,
            audio_player=self.audio
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

    def _on_encoder_release(self):
        """Handle encoder button release (short press)."""
        if self._encoder_held:
            return  # Was a hold, not a press

        self._record_activity()

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
            # select() returns False (back to menu), True (handled), or (seconds, label) for timer
            result = self.recipe_app.select()
            if result is False:
                self._change_state(AppState.MENU)
            elif isinstance(result, tuple):
                # Timer button pressed - create the timer
                seconds, label = result
                self._create_timer_from_recipe(seconds, label)
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
        """Handle volume up button."""
        self._record_activity()
        new_volume = self.audio.volume_up()
        self._show_volume_overlay(new_volume)

    def _on_volume_down(self):
        """Handle volume down button."""
        self._record_activity()
        new_volume = self.audio.volume_down()
        self._show_volume_overlay(new_volume)

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
                # Create and start a timer
                minutes = params.get("minutes", 5)
                print(f"  [Voice: Starting {minutes} minute timer]")
                total_seconds = minutes * 60
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
            if use_current and self.recipe_app.current_recipe:
                recipe = self.recipe_app.current_recipe
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
                    self.tts.speak_async("No recipe is currently open.")
                else:
                    self.tts.speak_async(f"Sorry, I couldn't find a recipe for {recipe_name}.")
                print(f"  [Voice: Recipe not found]")

        elif intent == "recipe_oven_time":
            # Get oven/baking time from recipe instructions
            use_current = params.get("use_current", False)
            recipe_name = params.get("name", "")

            recipe = None
            if use_current and self.recipe_app.current_recipe:
                recipe = self.recipe_app.current_recipe
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
                    self.tts.speak_async("No recipe is currently open.")
                else:
                    self.tts.speak_async(f"Sorry, I couldn't find a recipe for {recipe_name}.")
                print(f"  [Voice: Recipe not found]")

        elif intent == "recipe_temperature":
            # Get oven temperature for a recipe
            use_current = params.get("use_current", False)
            recipe_name = params.get("name", "")

            recipe = None
            if use_current and self.recipe_app.current_recipe:
                recipe = self.recipe_app.current_recipe
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
                    self.tts.speak_async("No recipe is currently open.")
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

        # === SPOTIFY COMMANDS (paused - Spotify API unavailable) ===
        elif intent == "spotify_play":
            query = params.get("query", "")
            print(f"  [Voice: Play request for '{query}' - Spotify API not available]")
            self.tts.speak_async("Sorry, Spotify control isn't available right now.")
            self._change_state(AppState.MUSIC)
            self._schedule_update()

        elif intent == "spotify_pause":
            print(f"  [Voice: Pause music - Spotify API not available]")
            self.tts.speak_async("Sorry, Spotify control isn't available right now.")

        elif intent == "spotify_skip":
            print(f"  [Voice: Skip track - Spotify API not available]")
            self.tts.speak_async("Sorry, Spotify control isn't available right now.")

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

        # Reset music timeout when entering music app
        if new_state == AppState.MUSIC:
            self.last_music_playing_time = time.time()

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

    # ==================== Callbacks ====================

    def _on_timer_update(self):
        """Callback when timer app needs update."""
        self._schedule_update()

    def _on_music_update(self):
        """Callback when music app needs update (track change)."""
        # Check if Spotify just connected
        self._check_spotify_connection()
        self._schedule_update()

    def _on_music_progress_update(self):
        """Callback for music progress partial refresh."""
        # Disabled - partial refresh of just progress bar was causing display issues
        # Full screen refresh is more reliable
        pass

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

    def _render(self):
        """Render the current state to the display."""
        # Clear-first: do a white partial refresh before rendering content
        # This helps clear dark pixels without a full blink
        if self._needs_clear_first and not self._needs_full_refresh:
            # Must be in partial mode before calling display_Partial
            if not self.renderer.in_partial_mode:
                self.renderer.init_partial()
            white_img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
            white_buf = self.renderer.epd.getbuffer(white_img)
            self.renderer.epd.display_Partial(white_buf, 0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT)
            self._needs_clear_first = False
            print("  [Clear refresh]")

        # Timer alarm takes priority
        if self.timer_alarm_active:
            self.timer_app._render_alarm()
        elif self.state == AppState.HOME:
            self.home_app.render()
        elif self.state == AppState.MENU:
            self.menu_app.render()
        elif self.state == AppState.RECIPES:
            self.recipe_app.render()
        elif self.state == AppState.TIMERS:
            self.timer_app.render()
        elif self.state == AppState.MUSIC:
            self.music_app.render()

        # Draw volume overlay on framebuffer if active
        if self.volume_overlay_active:
            self._draw_volume_on_framebuffer()

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
                if self.state == AppState.HOME and not self.timer_alarm_active:
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

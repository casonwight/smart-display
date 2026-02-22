"""Timer app - create and manage countdown timers."""

import os
import time
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable
from PIL import Image, ImageDraw, ImageFont

from display.renderer import DisplayRenderer
from audio.player import AudioPlayer
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, ASSETS_DIR


class TimerState(Enum):
    LIST = 0       # Viewing timer list
    NEW = 1        # Creating new timer
    ALARM = 2      # Timer going off
    DETAIL = 3     # Viewing single timer detail
    ADD_TIME = 4   # Adding time to a timer


@dataclass
class Timer:
    id: str
    total_seconds: int
    remaining_seconds: int
    label: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    paused: bool = False

    def tick(self) -> bool:
        """Decrement timer by 1 second. Returns True if timer just completed."""
        if self.paused or self.remaining_seconds <= 0:
            return False
        self.remaining_seconds -= 1
        return self.remaining_seconds == 0

    def format_remaining(self) -> str:
        """Format remaining time in minutes."""
        total_minutes = (self.remaining_seconds + 59) // 60  # Round up
        if total_minutes == 0:
            return "< 1 min"
        return f"{total_minutes} min"

    def format_remaining_detailed(self) -> str:
        """Format remaining time as MM:SS."""
        minutes = self.remaining_seconds // 60
        seconds = self.remaining_seconds % 60
        return f"{minutes}:{seconds:02d}"

    def is_complete(self) -> bool:
        return self.remaining_seconds <= 0


class TimerApp:
    MAX_TIMERS = 5      # Maximum number of concurrent timers
    MAX_VISIBLE = 4
    ITEM_HEIGHT = 70
    LIST_START_Y = 70
    LIST_REGION_HEIGHT = 280

    def __init__(self, renderer: DisplayRenderer, audio: AudioPlayer):
        self.renderer = renderer
        self.audio = audio

        self.state = TimerState.LIST
        self.timers: list[Timer] = []
        self.selected_index = 0
        self.prev_selected_index = 0
        self.scroll_offset = 0

        # For creating new timer (minutes only)
        self.new_timer_minutes = 5

        # For detail view
        self.viewing_timer: Optional[Timer] = None
        self.detail_option = 0  # 0=pause/play, 1=add time, 2=cancel

        # For adding time
        self.add_time_minutes = 5

        # Alarm state
        self.alarming_timer: Optional[Timer] = None
        self.alarm_add_minutes = 0  # Minutes to add when dismissing (0 = just dismiss)
        self._pre_alarm_volume = None  # Volume before alarm boosted it
        self._spotify_was_killed = False  # Whether we killed Spotify for the alarm

        # Prevent double-press on state change
        self._last_state_change = 0
        self._state_change_cooldown = 0.3  # 300ms cooldown

        # Timer tick thread
        self._running = True
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

        # Callback for when display needs update
        self.on_update: Optional[Callable] = None

        # Set by main.py before render() when mini player occupies y=430-480
        self.mini_player_active = False

        # Alarm sound path
        self.alarm_sound = os.path.join(ASSETS_DIR, "alarm-noise.mp3")

        # Load timer done icon
        self.timer_done_icon = self._load_timer_done_icon()

        # Register region for timer list (for partial refresh)
        self.renderer.add_region("timer_list", 0, self.LIST_START_Y, DISPLAY_WIDTH, self.LIST_REGION_HEIGHT)

    def _load_timer_done_icon(self):
        """Load the timer done icon for the alarm screen."""
        icon_path = os.path.join(ASSETS_DIR, "app-icons", "timer-done-icon.png")
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
            # Crop 15% from edges to remove whitespace, then scale up
            w, h = img.size
            margin_x = int(w * 0.15)
            margin_y = int(h * 0.15)
            img = img.crop((margin_x, margin_y, w - margin_x, h - margin_y))
            img.thumbnail((300, 300), Image.Resampling.LANCZOS)
            return img.convert('L').convert('1')
        return None

    def _tick_loop(self):
        """Background thread to tick timers every second."""
        while self._running:
            time.sleep(1)
            completed = []
            for timer in self.timers:
                if timer.tick():
                    completed.append(timer)

            # Handle completed timers
            for timer in completed:
                self._on_timer_complete(timer)

            # Request display update if any timers are active (not paused)
            active_timers = [t for t in self.timers if not t.paused and t.remaining_seconds > 0]
            if active_timers and self.on_update:
                self.on_update()

    def _on_timer_complete(self, timer: Timer):
        """Handle timer completion."""
        self.alarming_timer = timer
        self.viewing_timer = None
        self.state = TimerState.ALARM
        self._play_alarm()
        if self.on_update:
            self.on_update()

    def _play_alarm(self):
        """Play alarm sound - stops Spotify, plays alarm."""
        import subprocess

        self.audio.sync_volume_from_system()
        self._pre_alarm_volume = self.audio.volume

        # Kill librespot to free the audio device (it will auto-restart after alarm)
        subprocess.run(["pkill", "-9", "librespot"], capture_output=True)
        self._spotify_was_killed = True
        print(f"  [Alarm: killed Spotify to play alarm]")

        # Give it a moment to release the device
        time.sleep(0.3)

        # Set alarm volume (min 80% so it's audible)
        alarm_volume = max(80, self._pre_alarm_volume)
        self.audio.volume = alarm_volume
        print(f"  [Alarm: set volume to {alarm_volume}% (was {self._pre_alarm_volume}%)]")

        print(f"  [Playing alarm sound: {self.alarm_sound}]")
        if os.path.exists(self.alarm_sound):
            self.audio.play_file(self.alarm_sound, blocking=False)
        else:
            print(f"  [Alarm file not found, using fallback]")
            self.audio.timer_alarm(repeats=3)

    def stop_alarm(self, add_minutes: int = 0):
        """Stop the alarm. If add_minutes > 0, create a new timer with that duration."""
        import subprocess

        # Stop the alarm sound
        self.audio.stop_playback()

        # Restore volume if it was boosted for alarm
        if self._pre_alarm_volume is not None:
            self.audio.volume = self._pre_alarm_volume
            print(f"  [Alarm: restored volume to {self._pre_alarm_volume}%]")
            self._pre_alarm_volume = None

        # Spotify will auto-restart via systemd after we killed it
        if self._spotify_was_killed:
            print(f"  [Alarm: Spotify will auto-restart]")
            self._spotify_was_killed = False

        if self.alarming_timer:
            if self.alarming_timer in self.timers:
                self.timers.remove(self.alarming_timer)

            # Create new timer if requested
            if add_minutes > 0:
                total_seconds = add_minutes * 60
                new_timer = Timer(
                    id=str(uuid.uuid4()),
                    total_seconds=total_seconds,
                    remaining_seconds=total_seconds
                )
                self.timers.append(new_timer)

            self.alarming_timer = None

        self.alarm_add_minutes = 0
        self.state = TimerState.LIST
        self._update_selection()

    def _update_selection(self):
        """Ensure selection is valid after timer removal."""
        if not self.timers:
            self.selected_index = 0
        else:
            self.selected_index = min(self.selected_index, len(self.timers))

    def _get_fonts(self):
        """Load fonts for rendering."""
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            time_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
            item_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except:
            title_font = time_font = item_font = small_font = ImageFont.load_default()
        return title_font, time_font, item_font, small_font

    def _render_list_area(self) -> Image.Image:
        """Render just the timer list area for partial refresh."""
        img = Image.new('1', (DISPLAY_WIDTH, self.LIST_REGION_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        _, _, item_font, _ = self._get_fonts()

        # Total items: back button (0) + timers + add timer (if not at max) + delete all (if timers exist)
        has_timers = len(self.timers) > 0
        can_add_timer = len(self.timers) < self.MAX_TIMERS
        total_items = 1 + len(self.timers) + (1 if can_add_timer else 0) + (1 if has_timers else 0)
        visible_start = self.scroll_offset
        visible_end = min(visible_start + self.MAX_VISIBLE, total_items)

        for i, idx in enumerate(range(visible_start, visible_end)):
            y = i * self.ITEM_HEIGHT
            is_selected = (idx == self.selected_index)

            # Leave 50px on right for scroll indicators
            box_right = DISPLAY_WIDTH - 50

            if idx == 0:
                # Back button
                if is_selected:
                    draw.rounded_rectangle([10, y, box_right, y + self.ITEM_HEIGHT - 5],
                                         radius=10, outline=0, width=3)
                draw.text((30, y + 18), "← Back", font=item_font, fill=0)
            elif idx <= len(self.timers):
                # Timer (index 1 to len(timers))
                timer = self.timers[idx - 1]
                if is_selected:
                    draw.rounded_rectangle([10, y, box_right, y + self.ITEM_HEIGHT - 5],
                                         radius=10, outline=0, width=3)

                # Timer time and optional label
                time_str = timer.format_remaining()
                if timer.paused:
                    time_str += " (paused)"
                if timer.label:
                    # Show label truncated if needed, then time
                    max_label_len = 18
                    label = timer.label[:max_label_len] + "..." if len(timer.label) > max_label_len else timer.label
                    time_str = f"{label}: {time_str}"
                draw.text((30, y + 15), time_str, font=item_font, fill=0)

                # Progress indicator
                if timer.total_seconds > 0:
                    progress = timer.remaining_seconds / timer.total_seconds
                    bar_width = 180
                    bar_x = box_right - bar_width - 20
                    bar_y = y + 25
                    draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + 15], outline=0, width=2)
                    fill_width = int(bar_width * progress)
                    if fill_width > 0:
                        draw.rectangle([bar_x, bar_y, bar_x + fill_width, bar_y + 15], fill=0)
            elif can_add_timer and idx == len(self.timers) + 1:
                # "Add Timer" option (only shown if not at max)
                if is_selected:
                    draw.rounded_rectangle([10, y, box_right, y + self.ITEM_HEIGHT - 5],
                                         radius=10, outline=0, width=3)
                draw.text((30, y + 18), "+ Add Timer", font=item_font, fill=0)
            else:
                # "Delete All" option (only shown if timers exist)
                if is_selected:
                    draw.rounded_rectangle([10, y, box_right, y + self.ITEM_HEIGHT - 5],
                                         radius=10, outline=0, width=3)
                draw.text((30, y + 18), "✕ Delete All Timers", font=item_font, fill=0)

        # Scroll indicators (positioned at edges, outside item boxes)
        if self.scroll_offset > 0:
            draw.text((DISPLAY_WIDTH - 30, 5), "▲", font=item_font, fill=0)
        if visible_end < total_items:
            draw.text((DISPLAY_WIDTH - 30, self.LIST_REGION_HEIGHT - 30), "▼", font=item_font, fill=0)

        return img

    def _render_list(self):
        """Render the timer list view."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        title_font, _, _, small_font = self._get_fonts()

        # Title
        draw.text((20, 15), "Timers", font=title_font, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Timer list area
        list_img = self._render_list_area()
        img.paste(list_img, (0, self.LIST_START_Y))

        # Footer (hidden when mini player occupies the bottom strip)
        if not self.mini_player_active:
            draw.line([(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)], fill=0, width=1)
            hint = "Turn: Navigate   Press: Select   Hold: Home"
            bbox = draw.textbbox((0, 0), hint, font=small_font)
            hint_x = (DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((hint_x, DISPLAY_HEIGHT - 38), hint, font=small_font, fill=0)

        self.renderer.framebuffer = img

    def _render_new_timer(self):
        """Render the new timer creation view."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        title_font, time_font, item_font, small_font = self._get_fonts()

        # Title
        draw.text((20, 15), "New Timer", font=title_font, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Fixed box size for consistent layout
        box_width = 120
        box_height = 90
        box_x = (DISPLAY_WIDTH - box_width) // 2
        box_y = DISPLAY_HEIGHT // 2 - 70

        # Draw box first
        draw.rounded_rectangle([box_x, box_y, box_x + box_width, box_y + box_height],
                             radius=10, outline=0, width=3)

        # Get text dimensions for centering within box
        min_str = f"{self.new_timer_minutes}"
        min_bbox = draw.textbbox((0, 0), min_str, font=time_font)
        min_text_width = min_bbox[2] - min_bbox[0]
        min_text_height = min_bbox[3] - min_bbox[1]

        # Center text in box (account for bbox offset)
        text_x = box_x + (box_width - min_text_width) // 2 - min_bbox[0]
        text_y = box_y + (box_height - min_text_height) // 2 - min_bbox[1]
        draw.text((text_x, text_y), min_str, font=time_font, fill=0)

        # Label - centered below the box
        label = "minutes"
        label_bbox = draw.textbbox((0, 0), label, font=item_font)
        label_width = label_bbox[2] - label_bbox[0]
        label_x = (DISPLAY_WIDTH - label_width) // 2
        draw.text((label_x, box_y + box_height + 15), label, font=item_font, fill=0)

        # Footer
        draw.line([(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)], fill=0, width=1)
        hint = "Turn: Adjust   Press: Start Timer   Hold: Cancel"
        bbox = draw.textbbox((0, 0), hint, font=small_font)
        hint_x = (DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((hint_x, DISPLAY_HEIGHT - 38), hint, font=small_font, fill=0)

        self.renderer.framebuffer = img

    def _render_detail(self):
        """Render timer detail view with countdown and options."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        title_font, time_font, item_font, small_font = self._get_fonts()

        if not self.viewing_timer:
            self.state = TimerState.LIST
            return

        timer = self.viewing_timer

        # Title
        draw.text((20, 15), "Timer", font=title_font, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Big countdown display
        time_str = timer.format_remaining_detailed()
        time_bbox = draw.textbbox((0, 0), time_str, font=time_font)
        time_width = time_bbox[2] - time_bbox[0]
        time_x = (DISPLAY_WIDTH - time_width) // 2
        draw.text((time_x, 120), time_str, font=time_font, fill=0)

        # Options buttons
        options = [
            "← Back",
            "+ Add Time",
            "Cancel Timer"
        ]
        button_width = 220
        button_height = 50
        start_y = 230
        spacing = 60

        for i, opt in enumerate(options):
            y = start_y + i * spacing
            x = (DISPLAY_WIDTH - button_width) // 2

            if i == self.detail_option:
                draw.rounded_rectangle([x, y, x + button_width, y + button_height],
                                     radius=10, outline=0, width=3)
            else:
                draw.rounded_rectangle([x, y, x + button_width, y + button_height],
                                     radius=10, outline=0, width=1)

            opt_bbox = draw.textbbox((0, 0), opt, font=item_font)
            opt_width = opt_bbox[2] - opt_bbox[0]
            opt_x = x + (button_width - opt_width) // 2
            draw.text((opt_x, y + 10), opt, font=item_font, fill=0)

        # Footer
        draw.line([(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)], fill=0, width=1)
        hint = "Turn: Select   Press: Confirm   Hold: Home"
        bbox = draw.textbbox((0, 0), hint, font=small_font)
        hint_x = (DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((hint_x, DISPLAY_HEIGHT - 38), hint, font=small_font, fill=0)

        self.renderer.framebuffer = img

    def _render_add_time(self):
        """Render the add time picker."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        title_font, time_font, item_font, small_font = self._get_fonts()

        # Title
        draw.text((20, 15), "Add Time", font=title_font, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Fixed box size for consistent layout
        box_width = 120
        box_height = 90
        box_x = (DISPLAY_WIDTH - box_width) // 2
        box_y = DISPLAY_HEIGHT // 2 - 70

        # Draw box first
        draw.rounded_rectangle([box_x, box_y, box_x + box_width, box_y + box_height],
                             radius=10, outline=0, width=3)

        # Get text dimensions for centering within box
        if self.add_time_minutes == 0:
            min_str = "Cancel"
            display_font = item_font  # Smaller font for "Cancel"
        else:
            min_str = f"{self.add_time_minutes}"
            display_font = time_font
        min_bbox = draw.textbbox((0, 0), min_str, font=display_font)
        min_text_width = min_bbox[2] - min_bbox[0]
        min_text_height = min_bbox[3] - min_bbox[1]

        # Center text in box (account for bbox offset)
        text_x = box_x + (box_width - min_text_width) // 2 - min_bbox[0]
        text_y = box_y + (box_height - min_text_height) // 2 - min_bbox[1]
        draw.text((text_x, text_y), min_str, font=display_font, fill=0)

        # Label - centered below the box
        label = "minutes to add" if self.add_time_minutes > 0 else "exit without adding"
        label_bbox = draw.textbbox((0, 0), label, font=item_font)
        label_width = label_bbox[2] - label_bbox[0]
        label_x = (DISPLAY_WIDTH - label_width) // 2
        draw.text((label_x, box_y + box_height + 15), label, font=item_font, fill=0)

        # Footer
        draw.line([(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)], fill=0, width=1)
        hint = "Turn: Adjust   Press: Add Time   Hold: Cancel"
        bbox = draw.textbbox((0, 0), hint, font=small_font)
        hint_x = (DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((hint_x, DISPLAY_HEIGHT - 38), hint, font=small_font, fill=0)

        self.renderer.framebuffer = img

    def _render_alarm(self):
        """Render the alarm screen with option to add more time."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        title_font, time_font, item_font, small_font = self._get_fonts()

        # Layout: icon on left, text stacked on right
        # Draw timer done icon
        if self.timer_done_icon:
            icon_x = 30
            icon_y = (DISPLAY_HEIGHT - self.timer_done_icon.height) // 2 - 20
            img.paste(self.timer_done_icon, (icon_x, icon_y))
            text_x_start = icon_x + self.timer_done_icon.width + 30
        else:
            text_x_start = 50

        text_area_w = DISPLAY_WIDTH - text_x_start - 20

        # "TIMER!" text - centered in right area
        text = "TIMER!"
        bbox = draw.textbbox((0, 0), text, font=time_font)
        text_width = bbox[2] - bbox[0]
        text_x = text_x_start + (text_area_w - text_width) // 2
        draw.text((text_x, 60), text, font=time_font, fill=0)

        # Timer info - below "TIMER!"
        if self.alarming_timer:
            total_secs = self.alarming_timer.total_seconds
            if self.alarming_timer.label:
                info = f"{self.alarming_timer.label}"
            elif total_secs >= 60:
                info = f"{total_secs // 60} min timer complete"
            else:
                info = f"{total_secs} sec timer complete"
            bbox = draw.textbbox((0, 0), info, font=item_font)
            info_width = bbox[2] - bbox[0]
            info_x = text_x_start + (text_area_w - info_width) // 2
            draw.text((info_x, 140), info, font=item_font, fill=0)

        # Add time display (if any) - in right area
        if self.alarm_add_minutes > 0:
            add_text = f"+ {self.alarm_add_minutes} min"
            add_bbox = draw.textbbox((0, 0), add_text, font=time_font)
            add_width = add_bbox[2] - add_bbox[0]
            add_height = add_bbox[3] - add_bbox[1]
            box_x = text_x_start + (text_area_w - add_width) // 2 - 30
            box_y = 220
            box_w = add_width + 60
            box_h = add_height + 40
            draw.rounded_rectangle(
                [box_x, box_y, box_x + box_w, box_y + box_h],
                radius=10, outline=0, width=3
            )
            text_y = box_y + (box_h - add_height) // 2 - add_bbox[1]
            draw.text((text_x_start + (text_area_w - add_width) // 2, text_y), add_text, font=time_font, fill=0)
            action_text = "Press to start new timer"
        else:
            action_text = "Press to dismiss"

        # Action instruction - centered at bottom
        bbox = draw.textbbox((0, 0), action_text, font=item_font)
        action_width = bbox[2] - bbox[0]
        draw.rounded_rectangle(
            [(DISPLAY_WIDTH - action_width) // 2 - 20, 340,
             (DISPLAY_WIDTH + action_width) // 2 + 20, 395],
            radius=10, outline=0, width=3
        )
        draw.text(((DISPLAY_WIDTH - action_width) // 2, 353), action_text, font=item_font, fill=0)

        # Footer hint
        draw.line([(0, DISPLAY_HEIGHT - 50), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 50)], fill=0, width=1)
        hint = "Turn: Add time   Press: Confirm   Hold: Dismiss"
        bbox = draw.textbbox((0, 0), hint, font=small_font)
        hint_x = (DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((hint_x, DISPLAY_HEIGHT - 38), hint, font=small_font, fill=0)

        self.renderer.framebuffer = img

    def render(self):
        """Render based on current state."""
        if self.state == TimerState.LIST:
            self._render_list()
        elif self.state == TimerState.NEW:
            self._render_new_timer()
        elif self.state == TimerState.DETAIL:
            self._render_detail()
        elif self.state == TimerState.ADD_TIME:
            self._render_add_time()
        elif self.state == TimerState.ALARM:
            self._render_alarm()

    def navigate(self, direction: int):
        """Handle encoder rotation."""
        if self.state == TimerState.LIST:
            # Navigate timer list: back (0) + timers (1 to N) + add timer (if not at max) + delete all (if timers exist)
            self.prev_selected_index = self.selected_index
            has_timers = len(self.timers) > 0
            can_add_timer = len(self.timers) < self.MAX_TIMERS
            max_idx = len(self.timers) + (1 if can_add_timer else 0) + (1 if has_timers else 0)
            self.selected_index = max(0, min(self.selected_index + direction, max_idx))

            # Scroll if needed
            if self.selected_index < self.scroll_offset:
                self.scroll_offset = self.selected_index
            elif self.selected_index >= self.scroll_offset + self.MAX_VISIBLE:
                self.scroll_offset = self.selected_index - self.MAX_VISIBLE + 1

        elif self.state == TimerState.NEW:
            # Adjust minutes (1-99)
            self.new_timer_minutes = max(1, min(99, self.new_timer_minutes + direction))

        elif self.state == TimerState.DETAIL:
            # Navigate options
            self.detail_option = max(0, min(2, self.detail_option + direction))

        elif self.state == TimerState.ADD_TIME:
            # Adjust minutes (0-99, 0 = cancel/exit)
            self.add_time_minutes = max(0, min(99, self.add_time_minutes + direction))

        elif self.state == TimerState.ALARM:
            # Adjust add-time minutes (0 = dismiss, 1-99 = add new timer)
            self.alarm_add_minutes = max(0, min(99, self.alarm_add_minutes + direction))

    def select(self) -> bool:
        """Handle encoder press. Returns False to exit to menu."""
        # Check cooldown to prevent double-press on state change
        now = time.time()
        if now - self._last_state_change < self._state_change_cooldown:
            return True

        if self.state == TimerState.LIST:
            can_add_timer = len(self.timers) < self.MAX_TIMERS
            add_timer_idx = len(self.timers) + 1 if can_add_timer else -1
            delete_all_idx = len(self.timers) + (2 if can_add_timer else 1)

            if self.selected_index == 0:
                # Selected "Back" - return False to exit to menu
                return False
            elif self.selected_index <= len(self.timers):
                # Selected a timer (index 1 to len(timers))
                self.viewing_timer = self.timers[self.selected_index - 1]
                self.detail_option = 0
                self.state = TimerState.DETAIL
                self._last_state_change = now
            elif can_add_timer and self.selected_index == add_timer_idx:
                # Selected "Add Timer"
                self.state = TimerState.NEW
                self.new_timer_minutes = 5
                self._last_state_change = now
            elif self.selected_index == delete_all_idx:
                # Selected "Delete All Timers"
                self.timers.clear()
                self.selected_index = 0
                self.scroll_offset = 0  # Reset scroll to top
                self._last_state_change = now

        elif self.state == TimerState.NEW:
            # Start the timer with the current minutes value
            total_seconds = self.new_timer_minutes * 60
            timer = Timer(
                id=str(uuid.uuid4()),
                total_seconds=total_seconds,
                remaining_seconds=total_seconds
            )
            self.timers.append(timer)
            self.state = TimerState.LIST
            # Select the newly added timer (after back button)
            self.selected_index = len(self.timers)
            self.prev_selected_index = -1
            self._last_state_change = now

        elif self.state == TimerState.DETAIL:
            if not self.viewing_timer:
                self.state = TimerState.LIST
                self._last_state_change = now
                return True

            if self.detail_option == 0:
                # Back to list
                self.viewing_timer = None
                self.state = TimerState.LIST
                self._last_state_change = now
                return True
            elif self.detail_option == 1:
                # Go to add time screen
                self.add_time_minutes = 5
                self.state = TimerState.ADD_TIME
                self._last_state_change = now
            elif self.detail_option == 2:
                # Cancel timer
                if self.viewing_timer in self.timers:
                    self.timers.remove(self.viewing_timer)
                self.viewing_timer = None
                self.state = TimerState.LIST
                self._update_selection()
                self._last_state_change = now

        elif self.state == TimerState.ADD_TIME:
            # Add the selected minutes to the timer (0 = cancel/exit)
            if self.viewing_timer and self.add_time_minutes > 0:
                self.viewing_timer.remaining_seconds += self.add_time_minutes * 60
                self.viewing_timer.total_seconds += self.add_time_minutes * 60
            self.state = TimerState.DETAIL
            self._last_state_change = now

        elif self.state == TimerState.ALARM:
            self.stop_alarm(add_minutes=self.alarm_add_minutes)
            self._last_state_change = now

        return True

    def back(self) -> bool:
        """Handle long press. Returns False if at top level."""
        now = time.time()
        if self.state == TimerState.NEW:
            self.state = TimerState.LIST
            self._last_state_change = now
            return True
        elif self.state == TimerState.DETAIL:
            self.viewing_timer = None
            self.state = TimerState.LIST
            self._last_state_change = now
            return True
        elif self.state == TimerState.ADD_TIME:
            # Go back to detail without adding time
            self.state = TimerState.DETAIL
            self._last_state_change = now
            return True
        elif self.state == TimerState.ALARM:
            self.stop_alarm()
            self._last_state_change = now
            return True
        return False  # At list view, exit to menu

    def shutdown(self):
        """Stop the timer thread."""
        self._running = False

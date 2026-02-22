"""Menu screen app - clean, modern app selection with icons."""

import math
import os
from datetime import datetime
from enum import Enum
from PIL import Image, ImageDraw, ImageFont

from display.renderer import DisplayRenderer
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, ASSETS_DIR
from audio.weather import get_weather, WeatherData


class MenuItem(Enum):
    RECIPES = 0
    TIMERS = 1
    MUSIC = 2


MENU_ITEMS = [
    {"id": MenuItem.RECIPES, "icon": "recipe-icon.png"},
    {"id": MenuItem.TIMERS, "icon": "timer-icon.png"},
    {"id": MenuItem.MUSIC, "icon": "music-icon.png"},
]

# Layout constants - optimized for 800px wide display with 3 icons
# Total width: 3*240 + 2*20 = 760px (20px margin on each side)
BOX_SIZE = 240
ICON_SIZE = 230  # Slightly smaller than box for padding
SPACING = 20  # Enough gap so selection box doesn't overlap previous position


class MenuApp:
    def __init__(self, renderer: DisplayRenderer):
        self.renderer = renderer
        self.selected_index = 0
        self.prev_selected_index = 0
        self.num_items = len(MENU_ITEMS)
        self.mini_player_active = False  # Set by main.py before render()
        self.icons = self._load_icons()

        # Calculate positions - icons moved up to leave room for time pill
        total_width = (BOX_SIZE * self.num_items) + (SPACING * (self.num_items - 1))
        self.start_x = (DISPLAY_WIDTH - total_width) // 2
        self.menu_y = 55  # Position with room for pill at bottom

        # Store box positions
        self.box_positions = []
        for i in range(self.num_items):
            x = self.start_x + i * (BOX_SIZE + SPACING)
            self.box_positions.append((x, self.menu_y))

        # Single region for entire menu area
        menu_width = total_width
        menu_height = BOX_SIZE
        self.renderer.add_region("menu_area", self.start_x, self.menu_y, menu_width, menu_height)

        # Weather cache
        self._weather_cache = None
        self._weather_cache_time = 0

    def _load_icons(self) -> dict:
        """Load and prepare icon images (pre-cropped 600x600)."""
        icons = {}
        icon_dir = os.path.join(ASSETS_DIR, "app-icons")

        for item in MENU_ITEMS:
            icon_path = os.path.join(icon_dir, item["icon"])
            if os.path.exists(icon_path):
                img = Image.open(icon_path)

                # Resize to icon size (icons are pre-cropped)
                img.thumbnail((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)

                # Convert to 1-bit for e-ink
                img = img.convert('L').convert('1')
                icons[item["id"]] = img
            else:
                icons[item["id"]] = None
        return icons

    def _get_cached_weather(self):
        """Get weather with caching."""
        import time
        now = time.time()
        if self._weather_cache is None or (now - self._weather_cache_time) > 900:
            self._weather_cache = get_weather()
            self._weather_cache_time = now
        return self._weather_cache

    def _weather_code_to_condition(self, code: int) -> str:
        """Convert WMO weather code to simple icon condition."""
        if code == 0:
            return "clear"
        elif code in (1, 2):
            return "partly_cloudy"
        elif code == 3:
            return "cloudy"
        elif code in (45, 48):
            return "cloudy"
        elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            return "rain"
        elif code in (71, 73, 75, 77, 85, 86):
            return "snow"
        elif code in (95, 96, 99):
            return "storm"
        return "cloudy"

    def _draw_weather_icon(self, draw: ImageDraw.Draw, cx: int, cy: int, size: int, condition: str):
        """Draw a simple weather icon centered at (cx, cy)."""
        r = size // 2

        if condition == "clear":
            draw.ellipse([cx - r//2, cy - r//2, cx + r//2, cy + r//2], fill=0)
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                x1 = cx + int((r//2 + 3) * math.cos(rad))
                y1 = cy + int((r//2 + 3) * math.sin(rad))
                x2 = cx + int((r - 2) * math.cos(rad))
                y2 = cy + int((r - 2) * math.sin(rad))
                draw.line([x1, y1, x2, y2], fill=0, width=2)
        elif condition == "partly_cloudy":
            sun_cx, sun_cy = cx + r//3, cy - r//4
            draw.ellipse([sun_cx - r//4, sun_cy - r//4, sun_cx + r//4, sun_cy + r//4], fill=0)
            self._draw_cloud_shape(draw, cx - r//6, cy + r//6, int(r * 0.8))
        elif condition == "cloudy":
            self._draw_cloud_shape(draw, cx, cy, r)
        elif condition == "rain":
            self._draw_cloud_shape(draw, cx, cy - r//4, int(r * 0.7))
            for i in range(-1, 2):
                x = cx + i * (r//3)
                draw.line([x, cy + r//4, x - 4, cy + r//2 + 4], fill=0, width=2)
        elif condition == "snow":
            self._draw_cloud_shape(draw, cx, cy - r//4, int(r * 0.7))
            for i in range(-1, 2):
                x = cx + i * (r//3)
                draw.ellipse([x - 2, cy + r//4, x + 2, cy + r//4 + 4], fill=0)
                draw.ellipse([x - 2 + r//6, cy + r//2, x + 2 + r//6, cy + r//2 + 4], fill=0)
        elif condition == "storm":
            self._draw_cloud_shape(draw, cx, cy - r//4, int(r * 0.7))
            bolt = [(cx + 2, cy + r//6), (cx - 4, cy + r//3), (cx + 2, cy + r//3), (cx - 6, cy + r//2 + 6)]
            draw.line(bolt, fill=0, width=2)
        else:
            self._draw_cloud_shape(draw, cx, cy, r)

    def _draw_cloud_shape(self, draw: ImageDraw.Draw, cx: int, cy: int, r: int):
        """Draw a simple cloud shape."""
        draw.ellipse([cx - r, cy - r//3, cx, cy + r//3], outline=0, width=2)
        draw.ellipse([cx - r//2, cy - r//2, cx + r//2, cy + r//4], outline=0, width=2)
        draw.ellipse([cx, cy - r//3, cx + r, cy + r//3], outline=0, width=2)
        draw.ellipse([cx - r + 1, cy - r//3 + 1, cx - 1, cy + r//3 - 1], fill=0)
        draw.ellipse([cx - r//2 + 1, cy - r//2 + 1, cx + r//2 - 1, cy + r//4 - 1], fill=0)
        draw.ellipse([cx + 1, cy - r//3 + 1, cx + r - 1, cy + r//3 - 1], fill=0)

    def _render_time_pill(self, draw: ImageDraw.Draw):
        """Render the time/date/weather pill at the bottom."""
        try:
            time_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 52)
            date_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
            weather_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
        except:
            time_font = date_font = weather_font = ImageFont.load_default()

        now = datetime.now()
        time_str = now.strftime("%I:%M %p").lstrip("0")
        date_str = now.strftime("%a, %b %-d")

        # Measure text
        time_bbox = draw.textbbox((0, 0), time_str, font=time_font)
        time_w = time_bbox[2] - time_bbox[0]
        time_h = time_bbox[3] - time_bbox[1]

        date_bbox = draw.textbbox((0, 0), date_str, font=date_font)
        date_w = date_bbox[2] - date_bbox[0]
        date_h = date_bbox[3] - date_bbox[1]

        left_w = max(time_w, date_w)
        left_h = time_h + 8 + date_h

        # Weather
        weather = self._get_cached_weather()
        icon_size = 38
        right_w, right_h = 0, 0
        weather_str = ""
        weather_condition = "cloudy"

        if weather:
            weather_str = f"{round(weather.temperature_f)}°"
            weather_condition = self._weather_code_to_condition(weather.weather_code)
            weather_bbox = draw.textbbox((0, 0), weather_str, font=weather_font)
            temp_text_w = weather_bbox[2] - weather_bbox[0]
            temp_text_h = weather_bbox[3] - weather_bbox[1]
            right_w = max(icon_size, temp_text_w)
            right_h = icon_size + 4 + temp_text_h

        # Pill dimensions
        divider_width = 2
        divider_margin = 20
        if weather:
            content_w = left_w + divider_margin + divider_width + divider_margin + right_w
        else:
            content_w = left_w
        content_h = max(left_h, right_h) if weather else left_h

        padding_x = 30
        padding_y = 18
        pill_w = content_w + padding_x * 2
        pill_h = content_h + padding_y * 2
        pill_radius = pill_h // 2

        # Position pill at bottom-left; move up when mini player occupies y=430-480
        pill_x = 30
        if self.mini_player_active:
            pill_y = 425 - pill_h  # Sit just above mini player
        else:
            pill_y = DISPLAY_HEIGHT - 30 - pill_h

        # Draw pill background
        draw.rounded_rectangle(
            [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
            radius=pill_radius,
            outline=0,
            width=2
        )

        # Left side content
        left_x = pill_x + padding_x
        left_center_y = pill_y + pill_h // 2

        # Time
        time_x = left_x + (left_w - time_w) // 2
        time_y = left_center_y - left_h // 2
        draw.text((time_x - time_bbox[0], time_y - time_bbox[1]), time_str, font=time_font, fill=0)

        # Date
        date_x = left_x + (left_w - date_w) // 2
        date_y = time_y + time_h + 8
        draw.text((date_x - date_bbox[0], date_y - date_bbox[1]), date_str, font=date_font, fill=0)

        # Divider and weather
        if weather:
            divider_x = left_x + left_w + divider_margin
            divider_top = pill_y + padding_y + 4
            divider_bottom = pill_y + pill_h - padding_y - 4
            draw.line([(divider_x, divider_top), (divider_x, divider_bottom)], fill=0, width=divider_width)

            right_x = divider_x + divider_width + divider_margin
            right_center_y = pill_y + pill_h // 2

            icon_cx = right_x + right_w // 2
            icon_cy = right_center_y - right_h // 2 + icon_size // 2
            self._draw_weather_icon(draw, icon_cx, icon_cy, icon_size, weather_condition)

            weather_bbox = draw.textbbox((0, 0), weather_str, font=weather_font)
            temp_text_w = weather_bbox[2] - weather_bbox[0]
            temp_x = right_x + (right_w - temp_text_w) // 2
            temp_y = icon_cy + icon_size // 2 + 4
            draw.text((temp_x - weather_bbox[0], temp_y - weather_bbox[1]), weather_str, font=weather_font, fill=0)

    def _render_menu_area(self) -> Image.Image:
        """Render the entire menu area (all icons + selected box)."""
        total_width = (BOX_SIZE * self.num_items) + (SPACING * (self.num_items - 1))
        img = Image.new('1', (total_width, BOX_SIZE), 1)
        draw = ImageDraw.Draw(img)

        # First pass: draw all icons
        for i in range(self.num_items):
            x = i * (BOX_SIZE + SPACING)
            item = MENU_ITEMS[i]
            icon = self.icons.get(item["id"])
            if icon:
                icon_x = x + (BOX_SIZE - icon.width) // 2
                icon_y = (BOX_SIZE - icon.height) // 2
                img.paste(icon, (icon_x, icon_y))

        # Second pass: draw selection box on top of icons
        x = self.selected_index * (BOX_SIZE + SPACING)
        draw.rounded_rectangle(
            [x - 2, -2, x + BOX_SIZE + 1, BOX_SIZE + 1],
            radius=22, outline=0, width=4
        )

        return img

    def render(self):
        """Render the full menu screen (initial render)."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        # Render menu area and paste it
        menu_img = self._render_menu_area()
        img.paste(menu_img, (self.start_x, self.menu_y))

        # Render time pill (repositions above mini player when mini_player_active)
        self._render_time_pill(draw)

        self.renderer.framebuffer = img

    def render_partial(self):
        """Render entire menu area with single partial refresh."""
        if self.prev_selected_index == self.selected_index:
            return

        menu_img = self._render_menu_area()
        self.renderer.update_region("menu_area", menu_img)

    def navigate(self, direction: int):
        """Navigate menu selection. direction: 1=next, -1=previous"""
        self.prev_selected_index = self.selected_index
        self.selected_index = (self.selected_index + direction) % self.num_items

    def get_selected(self) -> MenuItem:
        """Get the currently selected menu item."""
        return MENU_ITEMS[self.selected_index]["id"]

    def select(self) -> MenuItem:
        """Select current menu item. Returns the MenuItem enum."""
        return self.get_selected()

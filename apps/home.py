"""Home screen app - displays full-screen wallpaper with time/date/weather overlay."""

import math
import os
import random
import time as time_module
from datetime import datetime
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from display.renderer import DisplayRenderer
from config import ASSETS_DIR, DISPLAY_WIDTH, DISPLAY_HEIGHT
from audio.weather import get_weather, WeatherData

WALLPAPERS_DIR = os.path.join(ASSETS_DIR, "wallpapers")

# Cache weather to avoid API calls on every render
_weather_cache: Optional[WeatherData] = None
_weather_cache_time: float = 0
WEATHER_CACHE_DURATION = 900  # 15 minutes


def _get_cached_weather() -> Optional[WeatherData]:
    """Get weather with caching to avoid excessive API calls."""
    global _weather_cache, _weather_cache_time
    now = time_module.time()
    if _weather_cache is None or (now - _weather_cache_time) > WEATHER_CACHE_DURATION:
        _weather_cache = get_weather()
        _weather_cache_time = now
    return _weather_cache


def _weather_code_to_condition(code: int) -> str:
    """Convert WMO weather code to simple icon condition."""
    if code == 0:
        return "clear"
    elif code in (1, 2):
        return "partly_cloudy"
    elif code == 3:
        return "cloudy"
    elif code in (45, 48):
        return "cloudy"  # Fog
    elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    elif code in (71, 73, 75, 77, 85, 86):
        return "snow"
    elif code in (95, 96, 99):
        return "storm"
    return "cloudy"


class HomeApp:
    def __init__(self, renderer: DisplayRenderer):
        self.renderer = renderer
        self.last_render_key = ""
        self.wallpaper_1bit = None
        self.wallpaper_gray = None
        self._load_wallpaper()

    def _load_wallpaper(self):
        """Load a random wallpaper from the wallpapers directory."""
        wallpaper_path = None

        if os.path.isdir(WALLPAPERS_DIR):
            wallpapers = [f for f in os.listdir(WALLPAPERS_DIR)
                          if f.lower().endswith('.png') and os.path.isfile(os.path.join(WALLPAPERS_DIR, f))]
            if wallpapers:
                chosen = random.choice(wallpapers)
                wallpaper_path = os.path.join(WALLPAPERS_DIR, chosen)

        if not wallpaper_path:
            for name in ["otter-wallpaper.jpg", "otter-wallpaper.png", "otter.png", "otter.jpg"]:
                path = os.path.join(ASSETS_DIR, name)
                if os.path.exists(path):
                    wallpaper_path = path
                    break

        if wallpaper_path:
            img = Image.open(wallpaper_path)
            img_ratio = img.width / img.height
            screen_ratio = DISPLAY_WIDTH / DISPLAY_HEIGHT

            if img_ratio > screen_ratio:
                new_height = DISPLAY_HEIGHT
                new_width = int(img.width * (DISPLAY_HEIGHT / img.height))
            else:
                new_width = DISPLAY_WIDTH
                new_height = int(img.height * (DISPLAY_WIDTH / img.width))

            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            left = (new_width - DISPLAY_WIDTH) // 2
            top = (new_height - DISPLAY_HEIGHT) // 2
            img = img.crop((left, top, left + DISPLAY_WIDTH, top + DISPLAY_HEIGHT))

            self.wallpaper_gray = img.convert('L')
            self.wallpaper_1bit = self.wallpaper_gray.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
        else:
            placeholder = self._create_placeholder()
            self.wallpaper_1bit = placeholder
            self.wallpaper_gray = placeholder.convert('L')

    def _create_placeholder(self) -> Image.Image:
        """Create a placeholder image when no wallpaper exists."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        draw.rectangle([5, 5, DISPLAY_WIDTH - 6, DISPLAY_HEIGHT - 6], outline=0, width=2)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except:
            font = ImageFont.load_default()
        text = "Place wallpaper in assets/"
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2
        y = (DISPLAY_HEIGHT - (bbox[3] - bbox[1])) // 2
        draw.text((x, y), text, font=font, fill=0)
        return img

    def _get_fonts(self) -> tuple:
        """Get fonts for time, date, and weather."""
        try:
            time_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 52)
            date_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
            weather_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
        except:
            time_font = date_font = weather_font = ImageFont.load_default()
        return time_font, date_font, weather_font

    def _draw_weather_icon(self, draw: ImageDraw.Draw, cx: int, cy: int, size: int, condition: str):
        """Draw a simple weather icon centered at (cx, cy)."""
        r = size // 2

        if condition == "clear":
            # Sun: circle with rays
            draw.ellipse([cx - r//2, cy - r//2, cx + r//2, cy + r//2], fill=0)
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                x1 = cx + int((r//2 + 3) * math.cos(rad))
                y1 = cy + int((r//2 + 3) * math.sin(rad))
                x2 = cx + int((r - 2) * math.cos(rad))
                y2 = cy + int((r - 2) * math.sin(rad))
                draw.line([x1, y1, x2, y2], fill=0, width=2)

        elif condition == "partly_cloudy":
            # Small sun peeking from behind cloud
            sun_cx, sun_cy = cx + r//3, cy - r//4
            draw.ellipse([sun_cx - r//4, sun_cy - r//4, sun_cx + r//4, sun_cy + r//4], fill=0)
            # Cloud in front
            self._draw_cloud_shape(draw, cx - r//6, cy + r//6, int(r * 0.8))

        elif condition == "cloudy":
            self._draw_cloud_shape(draw, cx, cy, r)

        elif condition == "rain":
            # Cloud with rain drops
            self._draw_cloud_shape(draw, cx, cy - r//4, int(r * 0.7))
            # Rain lines
            for i in range(-1, 2):
                x = cx + i * (r//3)
                draw.line([x, cy + r//4, x - 4, cy + r//2 + 4], fill=0, width=2)

        elif condition == "snow":
            # Cloud with snowflakes (dots)
            self._draw_cloud_shape(draw, cx, cy - r//4, int(r * 0.7))
            for i in range(-1, 2):
                x = cx + i * (r//3)
                draw.ellipse([x - 2, cy + r//4, x + 2, cy + r//4 + 4], fill=0)
                draw.ellipse([x - 2 + r//6, cy + r//2, x + 2 + r//6, cy + r//2 + 4], fill=0)

        elif condition == "storm":
            # Cloud with lightning bolt
            self._draw_cloud_shape(draw, cx, cy - r//4, int(r * 0.7))
            # Lightning bolt
            bolt = [
                (cx + 2, cy + r//6),
                (cx - 4, cy + r//3),
                (cx + 2, cy + r//3),
                (cx - 6, cy + r//2 + 6),
            ]
            draw.line(bolt, fill=0, width=2)

        else:
            # Default: cloud
            self._draw_cloud_shape(draw, cx, cy, r)

    def _draw_cloud_shape(self, draw: ImageDraw.Draw, cx: int, cy: int, r: int):
        """Draw a simple cloud shape."""
        # Three overlapping circles to form cloud
        draw.ellipse([cx - r, cy - r//3, cx, cy + r//3], outline=0, width=2)
        draw.ellipse([cx - r//2, cy - r//2, cx + r//2, cy + r//4], outline=0, width=2)
        draw.ellipse([cx, cy - r//3, cx + r, cy + r//3], outline=0, width=2)
        # Fill inside
        draw.ellipse([cx - r + 1, cy - r//3 + 1, cx - 1, cy + r//3 - 1], fill=0)
        draw.ellipse([cx - r//2 + 1, cy - r//2 + 1, cx + r//2 - 1, cy + r//4 - 1], fill=0)
        draw.ellipse([cx + 1, cy - r//3 + 1, cx + r - 1, cy + r//3 - 1], fill=0)

    def _render_overlay(self, time_str: str, date_str: str, weather: WeatherData = None) -> Image.Image:
        """Render full screen with wallpaper and pill-shaped info overlay.

        Layout: [Time/Date] | [Weather icon/temp]
        """
        time_font, date_font, weather_font = self._get_fonts()

        # Measure text sizes
        temp_img = Image.new('L', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        time_bbox = temp_draw.textbbox((0, 0), time_str, font=time_font)
        time_w = time_bbox[2] - time_bbox[0]
        time_h = time_bbox[3] - time_bbox[1]

        date_bbox = temp_draw.textbbox((0, 0), date_str, font=date_font)
        date_w = date_bbox[2] - date_bbox[0]
        date_h = date_bbox[3] - date_bbox[1]

        # Left side: time stacked over date
        left_w = max(time_w, date_w)
        left_h = time_h + 8 + date_h  # 8px spacing between time and date

        # Weather (icon stacked over temp)
        weather_str = ""
        weather_condition = "cloudy"
        icon_size = 38
        right_w, right_h = 0, 0

        if weather:
            weather_str = f"{round(weather.temperature_f)}°"
            weather_condition = _weather_code_to_condition(weather.weather_code)
            weather_bbox = temp_draw.textbbox((0, 0), weather_str, font=weather_font)
            temp_text_w = weather_bbox[2] - weather_bbox[0]
            temp_text_h = weather_bbox[3] - weather_bbox[1]
            right_w = max(icon_size, temp_text_w)
            right_h = icon_size + 4 + temp_text_h  # icon over temp

        # Calculate pill dimensions
        divider_width = 2
        divider_margin = 20  # space on each side of divider

        if weather:
            content_w = left_w + divider_margin + divider_width + divider_margin + right_w
        else:
            content_w = left_w
        content_h = max(left_h, right_h) if weather else left_h

        padding_x = 30
        padding_y = 18
        pill_w = content_w + padding_x * 2
        pill_h = content_h + padding_y * 2
        pill_radius = pill_h // 2  # True pill shape

        # Position pill in bottom-left
        margin = 30
        pill_x = margin
        pill_y = DISPLAY_HEIGHT - margin - pill_h

        # Create pill mask
        pill_mask = Image.new('L', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 0)
        mask_draw = ImageDraw.Draw(pill_mask)
        mask_draw.rounded_rectangle(
            [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
            radius=pill_radius,
            fill=255
        )

        # Blur edges slightly for softer blend
        pill_mask = pill_mask.filter(ImageFilter.GaussianBlur(radius=6))

        # Boost center, cut off low values
        def boost_and_threshold(x):
            boosted = int(x * 2.0)
            if boosted < 20:
                return 0
            return min(255, boosted)
        pill_mask = pill_mask.point(boost_and_threshold)

        # Blend white pill area with wallpaper
        white_screen = Image.new('L', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 255)
        blended = Image.composite(white_screen, self.wallpaper_gray, pill_mask)

        # Draw content
        draw = ImageDraw.Draw(blended)

        # Left side content area
        left_x = pill_x + padding_x
        left_center_y = pill_y + pill_h // 2

        # Time (centered horizontally in left area, above center)
        time_x = left_x + (left_w - time_w) // 2
        time_y = left_center_y - left_h // 2
        draw.text((time_x - time_bbox[0], time_y - time_bbox[1]), time_str, font=time_font, fill=0)

        # Date (centered horizontally in left area, below time)
        date_x = left_x + (left_w - date_w) // 2
        date_y = time_y + time_h + 8
        draw.text((date_x - date_bbox[0], date_y - date_bbox[1]), date_str, font=date_font, fill=0)

        # Draw divider and weather if available
        if weather:
            # Vertical divider
            divider_x = left_x + left_w + divider_margin
            divider_top = pill_y + padding_y + 4
            divider_bottom = pill_y + pill_h - padding_y - 4
            draw.line([(divider_x, divider_top), (divider_x, divider_bottom)], fill=0, width=divider_width)

            # Right side: weather icon over temperature
            right_x = divider_x + divider_width + divider_margin
            right_center_y = pill_y + pill_h // 2

            # Weather icon (centered in right area, above center)
            icon_cx = right_x + right_w // 2
            icon_cy = right_center_y - right_h // 2 + icon_size // 2
            self._draw_weather_icon(draw, icon_cx, icon_cy, icon_size, weather_condition)

            # Temperature (centered in right area, below icon)
            weather_bbox = temp_draw.textbbox((0, 0), weather_str, font=weather_font)
            temp_text_w = weather_bbox[2] - weather_bbox[0]
            temp_x = right_x + (right_w - temp_text_w) // 2
            temp_y = icon_cy + icon_size // 2 + 4
            draw.text((temp_x - weather_bbox[0], temp_y - weather_bbox[1]), weather_str, font=weather_font, fill=0)

        # Convert to 1-bit
        return blended.convert('1', dither=Image.Dither.FLOYDSTEINBERG)

    def render(self):
        """Render the home screen with current wallpaper."""
        now = datetime.now()
        time_str = now.strftime("%I:%M %p").lstrip("0")
        date_str = now.strftime("%a, %b %-d")  # "Wed, Feb 4"

        weather = _get_cached_weather()

        self.last_render_key = f"{time_str}|{date_str}"
        self.renderer.framebuffer = self._render_overlay(time_str, date_str, weather)

    def update(self) -> bool:
        """Called periodically to update display. Returns True if changed."""
        now = datetime.now()
        time_str = now.strftime("%I:%M %p").lstrip("0")
        date_str = now.strftime("%a, %b %-d")  # "Wed, Feb 4"

        render_key = f"{time_str}|{date_str}"
        if render_key == self.last_render_key:
            return False

        weather = _get_cached_weather()

        self.last_render_key = render_key
        self.renderer.framebuffer = self._render_overlay(time_str, date_str, weather)
        return True

    def reload_wallpaper(self):
        """Reload the wallpaper image (call after placing new image)."""
        self._load_wallpaper()
        self.last_render_key = ""

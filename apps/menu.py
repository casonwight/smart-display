"""Menu screen app - clean, modern app selection with icons."""

import os
from enum import Enum
from PIL import Image, ImageDraw

from display.renderer import DisplayRenderer
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, ASSETS_DIR


class MenuItem(Enum):
    RECIPES = 0
    TIMERS = 1
    MUSIC = 2


MENU_ITEMS = [
    {"id": MenuItem.RECIPES, "icon": "recipe-icon.png"},
    {"id": MenuItem.TIMERS, "icon": "timer-icon.png"},
    {"id": MenuItem.MUSIC, "icon": "music-icon.png"},
]

# Layout constants
BOX_SIZE = 200
ICON_SIZE = 170
SPACING = 60


class MenuApp:
    def __init__(self, renderer: DisplayRenderer):
        self.renderer = renderer
        self.selected_index = 0
        self.prev_selected_index = 0
        self.num_items = len(MENU_ITEMS)
        self.icons = self._load_icons()

        # Calculate positions
        total_width = (BOX_SIZE * self.num_items) + (SPACING * (self.num_items - 1))
        self.start_x = (DISPLAY_WIDTH - total_width) // 2
        self.cy = DISPLAY_HEIGHT // 2
        self.menu_y = self.cy - BOX_SIZE // 2

        # Store box positions
        self.box_positions = []
        for i in range(self.num_items):
            x = self.start_x + i * (BOX_SIZE + SPACING)
            self.box_positions.append((x, self.menu_y))

        # Single region for entire menu area
        menu_width = total_width
        menu_height = BOX_SIZE
        self.renderer.add_region("menu_area", self.start_x, self.menu_y, menu_width, menu_height)

    def _load_icons(self) -> dict:
        """Load and prepare icon images."""
        icons = {}
        icon_dir = os.path.join(ASSETS_DIR, "app-icons")

        for item in MENU_ITEMS:
            icon_path = os.path.join(icon_dir, item["icon"])
            if os.path.exists(icon_path):
                img = Image.open(icon_path)

                # Convert to RGBA to handle transparency, then to grayscale
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')

                # Create grayscale version for content detection
                gray = img.convert('L')

                # Find bounding box of non-white content (threshold at 250)
                # This crops out the whitespace padding
                bbox = None
                pixels = gray.load()
                min_x, min_y = img.width, img.height
                max_x, max_y = 0, 0
                for y in range(img.height):
                    for x in range(img.width):
                        if pixels[x, y] < 250:  # Non-white pixel
                            min_x = min(min_x, x)
                            min_y = min(min_y, y)
                            max_x = max(max_x, x)
                            max_y = max(max_y, y)

                if max_x > min_x and max_y > min_y:
                    # Add small margin around content
                    margin = 10
                    min_x = max(0, min_x - margin)
                    min_y = max(0, min_y - margin)
                    max_x = min(img.width, max_x + margin)
                    max_y = min(img.height, max_y + margin)
                    img = img.crop((min_x, min_y, max_x, max_y))

                # Resize to fill the icon area
                img.thumbnail((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)

                # Convert to 1-bit for e-ink
                img = img.convert('L').convert('1')
                icons[item["id"]] = img
            else:
                icons[item["id"]] = None
        return icons

    def _render_menu_area(self) -> Image.Image:
        """Render the entire menu area (all icons + selected box)."""
        total_width = (BOX_SIZE * self.num_items) + (SPACING * (self.num_items - 1))
        img = Image.new('1', (total_width, BOX_SIZE), 1)
        draw = ImageDraw.Draw(img)

        for i in range(self.num_items):
            # Position within the menu area image
            x = i * (BOX_SIZE + SPACING)
            is_selected = (i == self.selected_index)

            # Draw box only if selected
            if is_selected:
                draw.rounded_rectangle(
                    [x + 2, 2, x + BOX_SIZE - 3, BOX_SIZE - 3],
                    radius=20, outline=0, width=4
                )

            # Draw icon centered
            item = MENU_ITEMS[i]
            icon = self.icons.get(item["id"])
            if icon:
                icon_x = x + (BOX_SIZE - icon.width) // 2
                icon_y = (BOX_SIZE - icon.height) // 2
                img.paste(icon, (icon_x, icon_y))

        return img

    def render(self):
        """Render the full menu screen (initial render)."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)

        # Render menu area and paste it
        menu_img = self._render_menu_area()
        img.paste(menu_img, (self.start_x, self.menu_y))

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

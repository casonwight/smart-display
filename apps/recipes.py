"""Recipe app - browse and display CookLang recipes."""

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple
from PIL import Image, ImageDraw, ImageFont

from display.renderer import DisplayRenderer
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, RECIPE_DIR


def extract_recipe_timings(content: str) -> list[Tuple[int, str]]:
    """Extract timing values from recipe content (not metadata).

    Returns list of (seconds, display_label) tuples.
    Excludes cook_time/prep_time from metadata.
    For ranges like "12-14", uses the lower value.
    """
    timings = []
    seen_seconds = set()  # Avoid duplicate timers

    # Skip metadata section (between --- markers)
    parts = content.split('---')
    if len(parts) >= 3:
        # Content is after second ---
        body = '---'.join(parts[2:])
    else:
        body = content

    # Find all ~{value%unit} patterns
    for match in re.finditer(r'~\{([^}]+)\}', body):
        timing_str = match.group(1)

        # Parse amount and unit (format: "value%unit")
        if '%' in timing_str:
            amount_str, unit = timing_str.split('%', 1)
        else:
            # No unit specified, assume minutes
            amount_str, unit = timing_str, 'minutes'

        # Handle ranges (e.g., "12-14" or "20-30") - use lower value
        amount_str = amount_str.strip()
        if '-' in amount_str or '–' in amount_str:
            # Split on dash or en-dash
            parts = re.split(r'[-–]', amount_str)
            try:
                amount = float(parts[0].strip())
            except ValueError:
                continue
        else:
            try:
                amount = float(amount_str)
            except ValueError:
                continue

        # Convert to seconds based on unit
        unit = unit.lower().strip()
        if unit.startswith('h'):  # hour, hours, hr, hrs
            seconds = int(amount * 3600)
            label = f"{int(amount)}h" if amount == int(amount) else f"{amount}h"
        elif unit.startswith('s'):  # second, seconds, sec
            seconds = int(amount)
            label = f"{int(amount)}s"
        else:  # minutes (default)
            seconds = int(amount * 60)
            label = f"{int(amount)}m" if amount == int(amount) else f"{amount}m"

        # Skip very short times (< 1 min) and duplicates
        if seconds >= 60 and seconds not in seen_seconds:
            seen_seconds.add(seconds)
            timings.append((seconds, label))

    return timings


class RecipeState(Enum):
    CATEGORIES = 0
    RECIPE_LIST = 1
    RECIPE_VIEW = 2


@dataclass
class Ingredient:
    name: str
    amount: str = ""
    unit: str = ""


@dataclass
class Section:
    name: str
    paragraphs: list = field(default_factory=list)


@dataclass
class Recipe:
    name: str
    metadata: dict = field(default_factory=dict)
    ingredients: list = field(default_factory=list)
    cookware: list = field(default_factory=list)
    sections: list = field(default_factory=list)


def parse_cooklang(content: str, name: str) -> Recipe:
    """Parse CookLang format with YAML front matter and sections."""
    recipe = Recipe(name=name)

    lines = content.strip().split('\n')

    # Parse YAML front matter (between --- markers)
    in_frontmatter = False
    frontmatter_done = False
    current_section = None
    current_paragraph = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Handle front matter
        if line.strip() == '---':
            if not in_frontmatter and not frontmatter_done:
                in_frontmatter = True
                i += 1
                continue
            elif in_frontmatter:
                in_frontmatter = False
                frontmatter_done = True
                i += 1
                continue

        if in_frontmatter:
            # Parse YAML-style metadata (supports multi-word keys like "cook time")
            match = re.match(r'([\w\s]+?):\s*(.+)', line)
            if match:
                key = match.group(1).strip().lower().replace(' ', '_')  # "cook time" -> "cook_time"
                value = match.group(2).strip()
                recipe.metadata[key] = value
                if key == 'title':
                    recipe.name = value
            i += 1
            continue

        # Handle section headers (== Name ==)
        section_match = re.match(r'==\s*(.+?)\s*==', line)
        if section_match:
            # Save previous section
            if current_section:
                if current_paragraph:
                    current_section.paragraphs.append(' '.join(current_paragraph))
                    current_paragraph = []
                recipe.sections.append(current_section)

            current_section = Section(name=section_match.group(1))
            i += 1
            continue

        # Skip empty lines (but they separate paragraphs)
        if not line.strip():
            if current_paragraph and current_section:
                current_section.paragraphs.append(' '.join(current_paragraph))
                current_paragraph = []
            i += 1
            continue

        # Parse ingredients from line @name{amount%unit}
        for match in re.finditer(r'@([\w\s-]+?)\{([^}]*)\}', line):
            ing_name = match.group(1).strip()
            details = match.group(2) or ""
            if '%' in details:
                amount, unit = details.split('%', 1)
            else:
                amount, unit = details, ""

            ingredient = Ingredient(name=ing_name, amount=amount, unit=unit)
            # Check if already exists
            exists = any(i.name == ingredient.name for i in recipe.ingredients)
            if not exists:
                recipe.ingredients.append(ingredient)

        # Parse cookware #name{}
        for match in re.finditer(r'#([\w\s-]+?)\{[^}]*\}', line):
            cookware = match.group(1).strip()
            if cookware not in recipe.cookware:
                recipe.cookware.append(cookware)

        # Clean line for display
        display_line = line
        # Replace @ingredient{amount%unit} with just ingredient name (including @? for optional)
        display_line = re.sub(r'@\??([\w\s-]+?)\{[^}]*\}', r'\1', display_line)
        # Replace #cookware{} with just cookware name
        display_line = re.sub(r'#([\w\s-]+?)\{[^}]*\}', r'\1', display_line)
        # Replace #cookware (without braces) with just cookware name
        display_line = re.sub(r'#([\w\s-]+?)(?=\s|$|,|\.)', r'\1', display_line)
        # Replace ~{time%unit} with readable time (convert % to space)
        def format_time(match):
            time_str = match.group(1).replace('%', ' ')
            return f'({time_str})'
        display_line = re.sub(r'~\{([^}]+)\}', format_time, display_line)
        # Remove parenthetical notes like (90°F to 95°F)
        display_line = re.sub(r'\([^)]*°[^)]*\)', '', display_line)
        # Clean up extra spaces
        display_line = re.sub(r'\s+', ' ', display_line).strip()

        if display_line:
            if current_section is None:
                current_section = Section(name="Instructions")
            current_paragraph.append(display_line)

        i += 1

    # Save final section
    if current_section:
        if current_paragraph:
            current_section.paragraphs.append(' '.join(current_paragraph))
        recipe.sections.append(current_section)

    return recipe


class RecipeApp:
    # Layout constants
    ITEM_HEIGHT = 60
    LIST_START_Y = 70
    MAX_VISIBLE = 5

    def __init__(self, renderer: DisplayRenderer):
        self.renderer = renderer
        self.recipe_dir = RECIPE_DIR

        self.state = RecipeState.CATEGORIES
        self.categories = []
        self.recipes_in_category = []
        self.current_category = ""
        self.current_recipe = None
        self.current_recipe_content = ""  # Raw content for timing extraction

        self.selected_index = 0
        self.prev_selected_index = 0
        self.scroll_offset = 0
        self.prev_scroll_offset = 0
        self.max_visible_items = self.MAX_VISIBLE

        # Recipe view footer state
        self.recipe_timings: list[Tuple[int, str]] = []  # (seconds, label) for timer buttons
        self.footer_selected = 0  # 0 = Back, 1+ = timer buttons
        self.footer_scroll = 0  # Horizontal scroll offset for footer buttons
        self.timers_added: set[int] = set()  # Indices of timer buttons that have been clicked

        # Load categories
        self._load_categories()

        # Register region for the entire list area (for partial refresh)
        list_height = self.ITEM_HEIGHT * self.MAX_VISIBLE
        self.renderer.add_region("recipe_list", 0, self.LIST_START_Y, DISPLAY_WIDTH, list_height)

    def _load_categories(self):
        """Load category folders from recipe directory."""
        self.categories = []
        if os.path.exists(self.recipe_dir):
            for item in sorted(os.listdir(self.recipe_dir)):
                path = os.path.join(self.recipe_dir, item)
                if os.path.isdir(path):
                    self.categories.append(item)

    def _load_recipes(self, category: str):
        """Load recipe files from a category folder."""
        self.recipes_in_category = []
        category_path = os.path.join(self.recipe_dir, category)
        if os.path.exists(category_path):
            for item in sorted(os.listdir(category_path)):
                if item.endswith('.cook'):
                    self.recipes_in_category.append(item[:-5])  # Remove .cook extension

    def _load_recipe(self, category: str, recipe_name: str) -> Recipe:
        """Load and parse a recipe file."""
        path = os.path.join(self.recipe_dir, category, f"{recipe_name}.cook")
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read()
            self.current_recipe_content = content
            self.recipe_timings = extract_recipe_timings(content)
            self.footer_selected = 0  # Reset to Back button
            self.footer_scroll = 0
            self.timers_added = set()  # Reset added timers for new recipe
            return parse_cooklang(content, recipe_name)
        return None

    def _get_fonts(self):
        """Load fonts for rendering."""
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            item_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
            body_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except:
            title_font = ImageFont.load_default()
            item_font = title_font
            body_font = title_font
            small_font = title_font
        return title_font, item_font, body_font, small_font

    def _format_name(self, name: str) -> str:
        """Format a filename into display name."""
        return name.replace('-', ' ').replace('_', ' ').title()

    def _render_list(self, title: str, items: list):
        """Render a list view (categories or recipes)."""
        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)
        title_font, item_font, _, small_font = self._get_fonts()

        # Draw title
        draw.text((20, 15), title, font=title_font, fill=0)
        draw.line([(0, 58), (DISPLAY_WIDTH, 58)], fill=0, width=2)

        # Draw list area
        list_img = self._render_list_area(items)
        img.paste(list_img, (0, self.LIST_START_Y))

        # Draw scroll indicators if needed (account for back button)
        total_items = len(items) + 1  # +1 for back button
        visible_end = min(self.scroll_offset + self.max_visible_items, total_items)
        if self.scroll_offset > 0:
            draw.text((DISPLAY_WIDTH - 40, 65), "^", font=item_font, fill=0)
        if visible_end < total_items:
            draw.text((DISPLAY_WIDTH - 40, DISPLAY_HEIGHT - 80), "v", font=item_font, fill=0)

        # Draw footer hint
        draw.line([(0, DISPLAY_HEIGHT - 55), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 55)], fill=0, width=2)
        hint = "Turn: Navigate   Press: Select   Hold: Home"
        bbox = draw.textbbox((0, 0), hint, font=small_font)
        hint_x = (DISPLAY_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((hint_x, DISPLAY_HEIGHT - 40), hint, font=small_font, fill=0)

        self.renderer.framebuffer = img

    def _render_list_area(self, items: list, show_back: bool = True) -> Image.Image:
        """Render just the list area (for partial refresh)."""
        list_height = self.ITEM_HEIGHT * self.MAX_VISIBLE
        img = Image.new('1', (DISPLAY_WIDTH, list_height), 1)
        draw = ImageDraw.Draw(img)
        _, item_font, _, _ = self._get_fonts()

        # Add back button as first item (index -1 offset)
        total_items = len(items) + (1 if show_back else 0)
        visible_start = self.scroll_offset
        visible_end = min(visible_start + self.max_visible_items, total_items)

        for i, idx in enumerate(range(visible_start, visible_end)):
            y = i * self.ITEM_HEIGHT
            is_selected = (idx == self.selected_index)

            if show_back and idx == 0:
                # Back button
                display_name = "← Back"
            else:
                # Regular item (offset by 1 if showing back button)
                item_idx = idx - (1 if show_back else 0)
                if item_idx < len(items):
                    display_name = self._format_name(items[item_idx])
                else:
                    continue

            if is_selected:
                draw.rounded_rectangle(
                    [10, y, DISPLAY_WIDTH - 10, y + self.ITEM_HEIGHT - 5],
                    radius=10, outline=0, width=3
                )

            draw.text((30, y + 15), display_name, font=item_font, fill=0)

        return img

    def _get_current_items(self) -> list:
        """Get the current list of items based on state."""
        if self.state == RecipeState.CATEGORIES:
            return self.categories
        elif self.state == RecipeState.RECIPE_LIST:
            return self.recipes_in_category
        return []

    def render_list_partial(self):
        """Render only the list area using partial refresh."""
        items = self._get_current_items()
        if not items:
            return

        list_img = self._render_list_area(items)
        self.renderer.update_region("recipe_list", list_img)

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
        """Wrap text to fit within max_width."""
        words = text.split()
        lines = []
        current_line = []

        temp_img = Image.new('1', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = temp_draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]

        if current_line:
            lines.append(' '.join(current_line))

        return lines

    def _build_recipe_content(self) -> list:
        """Build formatted recipe content as list of (text, style) tuples."""
        recipe = self.current_recipe
        content = []

        # Skip description - user only wants ingredients and steps

        # Ingredients section
        if recipe.ingredients:
            content.append(("INGREDIENTS", "header"))
            content.append(("", "space"))
            for ing in recipe.ingredients:
                if ing.amount and ing.unit:
                    line = f"{ing.amount} {ing.unit} {ing.name}"
                elif ing.amount:
                    line = f"{ing.amount} {ing.name}"
                else:
                    line = f"{ing.name}"
                content.append((line, "ingredient"))
            content.append(("", "space"))

        # Recipe sections (Dough, Folds, Bake, etc.)
        for section in recipe.sections:
            content.append((section.name.upper(), "header"))
            content.append(("", "space"))
            for para in section.paragraphs:
                content.append((para, "paragraph"))
                content.append(("", "space"))

        return content

    def _render_recipe(self):
        """Render the current recipe view with nice formatting."""
        if not self.current_recipe:
            return

        img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        # Load fonts
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
            header_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            body_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            meta_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", 18)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        except:
            title_font = header_font = body_font = meta_font = small_font = ImageFont.load_default()

        recipe = self.current_recipe

        # Draw title (fixed at top)
        title = self._format_name(recipe.name)
        draw.text((20, 12), title, font=title_font, fill=0)
        draw.line([(20, 50), (DISPLAY_WIDTH - 20, 50)], fill=0, width=2)

        # Build content
        content = self._build_recipe_content()

        # Calculate line heights and positions
        content_start_y = 60
        content_end_y = DISPLAY_HEIGHT - 50
        content_height = content_end_y - content_start_y
        line_height = 26
        max_text_width = DISPLAY_WIDTH - 50

        # Render content with wrapping
        rendered_lines = []
        for text, style in content:
            if style == "space":
                rendered_lines.append(("", "space", 10))  # Smaller gap
            elif style == "header":
                rendered_lines.append((text, "header", line_height + 4))
            elif style == "meta":
                rendered_lines.append((text, "meta", line_height))
            elif style == "description":
                wrapped = self._wrap_text(text, meta_font, max_text_width)
                for line in wrapped:
                    rendered_lines.append((line, "description", line_height - 4))
            elif style == "ingredient":
                rendered_lines.append((text, "ingredient", line_height - 4))
            elif style == "paragraph":
                # Wrap long paragraphs
                wrapped = self._wrap_text(text, body_font, max_text_width)
                for line in wrapped:
                    rendered_lines.append((line, "paragraph", line_height - 2))

        # Calculate total height and max scroll
        total_height = sum(h for _, _, h in rendered_lines)
        max_scroll_pixels = max(0, total_height - content_height)

        # Convert scroll offset to pixels (scroll_offset is in "units")
        scroll_pixels = min(self.scroll_offset * 20, max_scroll_pixels)

        # Draw visible content
        y = content_start_y - scroll_pixels
        for text, style, height in rendered_lines:
            # Skip if above visible area
            if y + height < content_start_y:
                y += height
                continue
            # Stop if below visible area
            if y > content_end_y:
                break

            # Only draw if in visible area
            if y >= content_start_y - height and y <= content_end_y:
                if style == "header":
                    draw.text((25, y), text, font=header_font, fill=0)
                elif style == "meta":
                    draw.text((25, y), text, font=meta_font, fill=0)
                elif style == "description":
                    draw.text((25, y), text, font=meta_font, fill=0)
                elif style == "ingredient":
                    # Draw bullet point
                    draw.ellipse([30, y + 8, 36, y + 14], fill=0)
                    draw.text((45, y), text.strip(), font=body_font, fill=0)
                elif style == "paragraph":
                    draw.text((25, y), text, font=body_font, fill=0)

            y += height

        # Clip content area (draw white rectangles over overflow)
        draw.rectangle([0, 0, DISPLAY_WIDTH, content_start_y - 1], fill=1)
        draw.rectangle([0, content_end_y + 1, DISPLAY_WIDTH, DISPLAY_HEIGHT], fill=1)

        # Redraw title area
        draw.text((20, 12), title, font=title_font, fill=0)
        draw.line([(20, 50), (DISPLAY_WIDTH - 20, 50)], fill=0, width=2)

        # Scroll indicators
        if scroll_pixels > 0:
            draw.polygon([(DISPLAY_WIDTH - 25, 70), (DISPLAY_WIDTH - 35, 85), (DISPLAY_WIDTH - 15, 85)], fill=0)
        if scroll_pixels < max_scroll_pixels:
            draw.polygon([(DISPLAY_WIDTH - 25, content_end_y - 10), (DISPLAY_WIDTH - 35, content_end_y - 25), (DISPLAY_WIDTH - 15, content_end_y - 25)], fill=0)

        # Footer with back button and timer buttons
        draw.line([(0, DISPLAY_HEIGHT - 45), (DISPLAY_WIDTH, DISPLAY_HEIGHT - 45)], fill=0, width=1)

        # Build footer buttons: Back + timer buttons
        footer_buttons = [("← Back", None)]  # (label, seconds or None for back)
        for seconds, label in self.recipe_timings:
            footer_buttons.append((label, seconds))

        # Calculate button positions
        button_padding = 8
        button_height = 30
        button_y = DISPLAY_HEIGHT - 40

        # Measure all buttons
        button_widths = []
        for label, _ in footer_buttons:
            bbox = draw.textbbox((0, 0), label, font=small_font)
            button_widths.append(bbox[2] - bbox[0] + 20)  # Add padding

        # Determine visible buttons based on scroll
        total_buttons = len(footer_buttons)
        visible_start = self.footer_scroll
        x = 15
        last_visible_idx = visible_start

        # Draw visible buttons
        for i in range(visible_start, total_buttons):
            label, _ = footer_buttons[i]
            btn_width = button_widths[i]

            # Stop if button would go past right edge (leave space for scroll indicator)
            if x + btn_width > DISPLAY_WIDTH - 30:
                break

            is_selected = (i == self.footer_selected)
            is_timer_added = (i > 0) and ((i - 1) in self.timers_added)

            if is_timer_added:
                # Timer already added - draw inverted (filled black, white text)
                draw.rounded_rectangle(
                    [x, button_y, x + btn_width, button_y + button_height],
                    radius=8, fill=0
                )
                text_x = x + 10
                draw.text((text_x, button_y + 3), label, font=small_font, fill=1)
            else:
                # Normal button
                outline_width = 3 if is_selected else 1
                draw.rounded_rectangle(
                    [x, button_y, x + btn_width, button_y + button_height],
                    radius=8, outline=0, width=outline_width
                )
                text_x = x + 10
                draw.text((text_x, button_y + 3), label, font=small_font, fill=0)

            x += btn_width + button_padding
            last_visible_idx = i

        # Show scroll indicators if needed
        if self.footer_scroll > 0:
            draw.text((5, button_y + 5), "<", font=small_font, fill=0)
        if last_visible_idx < total_buttons - 1:
            draw.text((DISPLAY_WIDTH - 15, button_y + 5), ">", font=small_font, fill=0)

        # Store for scrolling
        self._max_scroll = max_scroll_pixels // 20
        self._content_line_count = self._max_scroll + 10  # For compatibility
        self._max_visible_lines = 10

        self.renderer.framebuffer = img

    def render(self):
        """Render based on current state."""
        if self.state == RecipeState.CATEGORIES:
            self._render_list("Recipes", self.categories)
        elif self.state == RecipeState.RECIPE_LIST:
            self._render_list(self._format_name(self.current_category), self.recipes_in_category)
        elif self.state == RecipeState.RECIPE_VIEW:
            self._render_recipe()

    def navigate(self, direction: int):
        """Navigate list or scroll recipe."""
        if self.state == RecipeState.CATEGORIES:
            items = self.categories
            total_items = len(items) + 1  # +1 for back button
        elif self.state == RecipeState.RECIPE_LIST:
            items = self.recipes_in_category
            total_items = len(items) + 1  # +1 for back button
        elif self.state == RecipeState.RECIPE_VIEW:
            # Scroll the recipe content, but at bottom, cycle footer buttons
            max_scroll = max(0, self._content_line_count - self._max_visible_lines)
            total_footer_buttons = 1 + len(self.recipe_timings)  # Back + timer buttons

            if direction > 0:  # Scrolling down
                if self.scroll_offset >= max_scroll:
                    # At bottom of content - cycle footer buttons right
                    self.footer_selected = min(self.footer_selected + 1, total_footer_buttons - 1)
                    # Scroll footer to keep selection visible (simple: just follow selection)
                    if self.footer_selected > self.footer_scroll + 3:
                        self.footer_scroll = max(0, self.footer_selected - 3)
                else:
                    self.scroll_offset = min(self.scroll_offset + direction, max_scroll)
            else:  # Scrolling up
                if self.footer_selected > 0:
                    # Have a non-back button selected - cycle left first
                    self.footer_selected -= 1
                    # Scroll footer left if needed
                    if self.footer_selected < self.footer_scroll:
                        self.footer_scroll = self.footer_selected
                else:
                    # At Back button - scroll content up
                    self.scroll_offset = max(0, self.scroll_offset + direction)
            return
        else:
            return

        # Track previous state for partial refresh
        self.prev_selected_index = self.selected_index
        self.prev_scroll_offset = self.scroll_offset

        # Navigate list (including back button at index 0)
        self.selected_index = max(0, min(self.selected_index + direction, total_items - 1))

        # Adjust scroll offset to keep selection visible
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + self.max_visible_items:
            self.scroll_offset = self.selected_index - self.max_visible_items + 1

    def select(self) -> bool | Tuple[int, str]:
        """Select current item.

        Returns:
            False: go back to menu
            True: handled internally
            (seconds, label): timer to create - main.py should add this timer
        """
        # Index 0 is always the back button in list views
        if self.state == RecipeState.CATEGORIES:
            if self.selected_index == 0:
                # Back button selected - go back to menu
                return False
            elif self.categories:
                # Adjust for back button offset
                item_index = self.selected_index - 1
                if item_index < len(self.categories):
                    self.current_category = self.categories[item_index]
                    self._load_recipes(self.current_category)
                    self.state = RecipeState.RECIPE_LIST
                    self.selected_index = 0
                    self.scroll_offset = 0

        elif self.state == RecipeState.RECIPE_LIST:
            if self.selected_index == 0:
                # Back button selected - go back to categories
                self.state = RecipeState.CATEGORIES
                self.selected_index = 0
                self.scroll_offset = 0
            elif self.recipes_in_category:
                # Adjust for back button offset
                item_index = self.selected_index - 1
                if item_index < len(self.recipes_in_category):
                    recipe_name = self.recipes_in_category[item_index]
                    self.current_recipe = self._load_recipe(self.current_category, recipe_name)
                    self.state = RecipeState.RECIPE_VIEW
                    self.scroll_offset = 0

        elif self.state == RecipeState.RECIPE_VIEW:
            if self.footer_selected == 0:
                # Back button - go back to recipe list
                self.state = RecipeState.RECIPE_LIST
                self.scroll_offset = 0
            else:
                # Timer button selected - return timer info (if not already added)
                timer_idx = self.footer_selected - 1
                if timer_idx < len(self.recipe_timings) and timer_idx not in self.timers_added:
                    self.timers_added.add(timer_idx)  # Mark as added
                    seconds, label = self.recipe_timings[timer_idx]
                    recipe_name = self._format_name(self.current_recipe.name)
                    timer_label = f"{recipe_name} Timer"
                    return (seconds, timer_label)

        return True

    def back(self) -> bool:
        """Go back. Returns False if already at top level."""
        if self.state == RecipeState.RECIPE_VIEW:
            self.state = RecipeState.RECIPE_LIST
            self.scroll_offset = 0
            return True
        elif self.state == RecipeState.RECIPE_LIST:
            self.state = RecipeState.CATEGORIES
            self.selected_index = 0
            self.scroll_offset = 0
            return True
        return False  # At top level, can exit to menu

import hashlib
from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd7in5_V2

from .regions import Region, REGIONS
from config import DISPLAY_WIDTH, DISPLAY_HEIGHT, MAX_PARTIAL_REFRESHES


class DisplayRenderer:
    def __init__(self):
        self.epd = epd7in5_V2.EPD()
        self.width = DISPLAY_WIDTH
        self.height = DISPLAY_HEIGHT

        # Main framebuffer - white background
        self.framebuffer = Image.new('1', (self.width, self.height), 1)

        # Track regions
        self.regions: dict[str, Region] = dict(REGIONS)

        # Partial refresh counter
        self.partial_refresh_count = 0
        self.max_partial = MAX_PARTIAL_REFRESHES

        # State
        self.initialized = False
        self.in_partial_mode = False

    def init(self):
        """Initialize the display for full refresh mode."""
        self.epd.init()
        self.initialized = True
        self.in_partial_mode = False

    def init_partial(self):
        """Initialize the display for partial refresh mode."""
        self.epd.init_part()
        self.initialized = True
        self.in_partial_mode = True

    def clear(self):
        """Clear the display to white."""
        if not self.initialized:
            self.init()
        self.epd.Clear()
        self.framebuffer = Image.new('1', (self.width, self.height), 1)
        self.partial_refresh_count = 0
        # Reset all region hashes
        for region in self.regions.values():
            region.last_content_hash = ""
            region.dirty = False

    def get_draw(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        """Get the framebuffer and a draw object for it."""
        return self.framebuffer, ImageDraw.Draw(self.framebuffer)

    def update_region(self, region_name: str, image: Image.Image) -> bool:
        """
        Update a region's content in the framebuffer.
        Returns True if content changed, False if identical.
        """
        if region_name not in self.regions:
            raise ValueError(f"Unknown region: {region_name}")

        region = self.regions[region_name]

        # Resize image if needed
        if image.size != (region.width, region.height):
            image = image.resize((region.width, region.height))

        # Convert to 1-bit
        if image.mode != '1':
            image = image.convert('1')

        # Check if content changed
        new_hash = hashlib.md5(image.tobytes()).hexdigest()
        if new_hash == region.last_content_hash:
            return False

        # Update framebuffer
        self.framebuffer.paste(image, (region.x, region.y))
        region.last_content_hash = new_hash
        region.dirty = True
        return True

    def add_region(self, name: str, x: int, y: int, width: int, height: int) -> Region:
        """Add a custom region."""
        region = Region(name, x, y, width, height)
        self.regions[name] = region
        return region

    def remove_region(self, name: str):
        """Remove a custom region."""
        if name in REGIONS:
            raise ValueError(f"Cannot remove standard region: {name}")
        self.regions.pop(name, None)

    def render_region(self, region_name: str):
        """Partial refresh a single region."""
        if region_name not in self.regions:
            raise ValueError(f"Unknown region: {region_name}")

        region = self.regions[region_name]

        # Switch to partial mode if needed
        if not self.in_partial_mode:
            self.init_partial()

        # Crop region from framebuffer
        region_image = self.framebuffer.crop((
            region.x, region.y,
            region.x + region.width,
            region.y + region.height
        ))

        # Get buffer for this region
        buf = self._get_region_buffer(region_image)

        # Partial refresh
        self.epd.display_Partial(
            buf,
            region.x,
            region.y,
            region.x + region.width,
            region.y + region.height
        )

        region.dirty = False
        self.partial_refresh_count += 1

    def render_dirty_regions(self):
        """Render all regions marked as dirty."""
        dirty_regions = [r for r in self.regions.values() if r.dirty]

        if not dirty_regions:
            return

        # Check if we need a full refresh
        if self.should_full_refresh():
            self.full_refresh()
            return

        # Partial refresh each dirty region
        for region in dirty_regions:
            self.render_region(region.name)

    def full_refresh(self):
        """Full screen refresh - clears ghosting."""
        if not self.initialized or self.in_partial_mode:
            self.init()

        buf = self.epd.getbuffer(self.framebuffer)
        self.epd.display(buf)

        self.partial_refresh_count = 0

        # Mark all regions clean
        for region in self.regions.values():
            region.dirty = False

    def should_full_refresh(self) -> bool:
        """Check if we've hit the partial refresh limit."""
        return self.partial_refresh_count >= self.max_partial

    def force_full_refresh(self):
        """Force a full refresh regardless of counter."""
        self.full_refresh()

    def _get_region_buffer(self, image: Image.Image) -> bytearray:
        """Convert a PIL image to display buffer format."""
        if image.mode != '1':
            image = image.convert('1')

        buf = bytearray(image.tobytes('raw'))
        # Invert: PIL 0=black, 1=white; e-paper 0=white, 1=black
        for i in range(len(buf)):
            buf[i] ^= 0xFF
        return buf

    def sleep(self):
        """Put display in low-power sleep mode."""
        self.epd.sleep()
        self.initialized = False
        self.in_partial_mode = False

    def draw_text(self, region_name: str, text: str, font_size: int = 32,
                  align: str = "center", valign: str = "center"):
        """Helper to draw text in a region."""
        region = self.regions[region_name]
        img = Image.new('1', (region.width, region.height), 1)
        draw = ImageDraw.Draw(img)

        # Try to load a nice font, fall back to default
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except:
            font = ImageFont.load_default()

        # Get text bounding box
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Calculate position
        if align == "center":
            x = (region.width - text_width) // 2
        elif align == "right":
            x = region.width - text_width - 10
        else:
            x = 10

        if valign == "center":
            y = (region.height - text_height) // 2
        elif valign == "bottom":
            y = region.height - text_height - 10
        else:
            y = 10

        draw.text((x, y), text, font=font, fill=0)
        self.update_region(region_name, img)

    def show_test_pattern(self):
        """Display a test pattern to verify the display works."""
        # Create test image
        img = Image.new('1', (self.width, self.height), 1)
        draw = ImageDraw.Draw(img)

        # Draw border
        draw.rectangle([0, 0, self.width-1, self.height-1], outline=0, width=3)

        # Draw region boundaries
        for name, region in REGIONS.items():
            draw.rectangle(
                [region.x, region.y, region.x + region.width - 1, region.y + region.height - 1],
                outline=0, width=2
            )

        # Draw labels
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except:
            font = ImageFont.load_default()
            small_font = font

        # Header label
        draw.text((350, 15), "HEADER", font=font, fill=0)

        # Content label
        draw.text((320, 220), "CONTENT", font=font, fill=0)
        draw.text((280, 260), "800 x 360 pixels", font=small_font, fill=0)

        # Footer label
        draw.text((350, 435), "FOOTER", font=font, fill=0)

        # Corner markers
        draw.text((10, 10), "TL", font=small_font, fill=0)
        draw.text((self.width - 30, 10), "TR", font=small_font, fill=0)
        draw.text((10, self.height - 30), "BL", font=small_font, fill=0)
        draw.text((self.width - 30, self.height - 30), "BR", font=small_font, fill=0)

        # Display
        self.framebuffer = img
        self.full_refresh()

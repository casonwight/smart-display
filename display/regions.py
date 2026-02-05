from dataclasses import dataclass, field
import hashlib
from PIL import Image


@dataclass
class Region:
    name: str
    x: int
    y: int
    width: int
    height: int
    dirty: bool = False
    last_content_hash: str = ""

    def contains_point(self, px: int, py: int) -> bool:
        return (self.x <= px < self.x + self.width and
                self.y <= py < self.y + self.height)

    def compute_hash(self, image: Image.Image) -> str:
        return hashlib.md5(image.tobytes()).hexdigest()

    def mark_dirty(self):
        self.dirty = True

    def mark_clean(self):
        self.dirty = False


# Standard screen regions
REGIONS = {
    "header": Region("header", 0, 0, 800, 60),
    "content": Region("content", 0, 60, 800, 360),
    "footer": Region("footer", 0, 420, 800, 60),
}


def create_menu_regions(num_items: int, cols: int = 3) -> dict[str, Region]:
    """Create regions for menu items in a grid layout."""
    regions = {}
    content = REGIONS["content"]

    rows = (num_items + cols - 1) // cols
    item_width = content.width // cols
    item_height = content.height // rows

    for i in range(num_items):
        row = i // cols
        col = i % cols
        x = content.x + col * item_width
        y = content.y + row * item_height
        regions[f"menu_item_{i}"] = Region(
            f"menu_item_{i}", x, y, item_width, item_height
        )

    return regions


def create_list_regions(num_items: int, item_height: int = 60) -> dict[str, Region]:
    """Create regions for a vertical list of items."""
    regions = {}
    content = REGIONS["content"]

    for i in range(num_items):
        y = content.y + i * item_height
        if y + item_height <= content.y + content.height:
            regions[f"list_item_{i}"] = Region(
                f"list_item_{i}", content.x, y, content.width, item_height
            )

    return regions

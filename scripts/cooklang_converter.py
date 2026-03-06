"""Convert recipe-scrapers output to CookLang format."""

import re
from typing import Optional


def parse_ingredient(ingredient_str: str) -> tuple[str, str, str]:
    """Parse an ingredient string into (name, amount, unit).

    Examples:
        "2 cups all-purpose flour" -> ("all-purpose flour", "2", "cups")
        "1/2 teaspoon salt" -> ("salt", "1/2", "teaspoon")
        "3 large eggs" -> ("eggs", "3", "large")
        "salt to taste" -> ("salt", "", "to taste")
    """
    ingredient_str = ingredient_str.strip()

    # Common unit patterns
    units = [
        'cups?', 'tablespoons?', 'tbsp?', 'teaspoons?', 'tsp?',
        'pounds?', 'lbs?', 'ounces?', 'oz', 'grams?', 'g',
        'kilograms?', 'kg', 'ml', 'milliliters?', 'liters?', 'l',
        'pinch(?:es)?', 'dash(?:es)?', 'cloves?', 'slices?',
        'pieces?', 'cans?', 'packages?', 'bunches?', 'heads?',
        'stalks?', 'sprigs?', 'leaves?', 'large', 'medium', 'small'
    ]
    unit_pattern = '|'.join(units)

    # Pattern: amount [unit] ingredient
    # Amount can be: 1, 1/2, 1 1/2, 1.5, etc.
    amount_pattern = r'(\d+(?:\s*/\s*\d+|\s+\d+/\d+|\.\d+)?)'

    # Try to match: amount unit ingredient
    match = re.match(
        rf'^{amount_pattern}\s*({unit_pattern})?\s+(.+)$',
        ingredient_str,
        re.IGNORECASE
    )

    if match:
        amount = match.group(1).strip()
        unit = (match.group(2) or '').strip()
        name = match.group(3).strip()
        return (name, amount, unit)

    # Try to match: amount ingredient (no unit)
    match = re.match(rf'^{amount_pattern}\s+(.+)$', ingredient_str)
    if match:
        amount = match.group(1).strip()
        name = match.group(2).strip()
        return (name, amount, '')

    # No amount found, return as-is
    return (ingredient_str, '', '')


def sanitize_ingredient_name(name: str) -> str:
    """Sanitize ingredient name for CookLang syntax."""
    # Remove parenthetical notes at the end
    name = re.sub(r'\s*\([^)]*\)\s*$', '', name)
    # Replace special characters that might break syntax
    name = name.replace('{', '').replace('}', '').replace('%', '')
    # Trim whitespace
    return name.strip()


def format_cooklang_ingredient(name: str, amount: str, unit: str) -> str:
    """Format ingredient in CookLang syntax: @name{amount%unit}"""
    name = sanitize_ingredient_name(name)

    # Handle hyphenated/multi-word names
    name = name.replace(' ', ' ')  # Keep spaces, CookLang supports them

    if amount and unit:
        return f"@{name}{{{amount}%{unit}}}"
    elif amount:
        return f"@{name}{{{amount}}}"
    else:
        return f"@{name}{{}}"


def extract_time_minutes(time_str: str) -> Optional[int]:
    """Extract time in minutes from a string like '30 minutes' or '1 hour 30 minutes'."""
    if not time_str:
        return None

    total_minutes = 0

    # Find hours
    hours_match = re.search(r'(\d+)\s*(?:hours?|hrs?)', time_str, re.IGNORECASE)
    if hours_match:
        total_minutes += int(hours_match.group(1)) * 60

    # Find minutes
    mins_match = re.search(r'(\d+)\s*(?:minutes?|mins?)', time_str, re.IGNORECASE)
    if mins_match:
        total_minutes += int(mins_match.group(1))

    return total_minutes if total_minutes > 0 else None


def format_time_for_yaml(minutes: Optional[int]) -> str:
    """Format minutes as human-readable time for YAML."""
    if not minutes:
        return ''

    if minutes >= 60:
        hours = minutes // 60
        mins = minutes % 60
        if mins:
            return f"{hours} hour{'s' if hours > 1 else ''} {mins} minutes"
        return f"{hours} hour{'s' if hours > 1 else ''}"
    return f"{minutes} minutes"


def convert_to_cooklang(scraper) -> str:
    """Convert a recipe-scrapers object to CookLang format.

    Args:
        scraper: A recipe_scrapers scraper object with methods like
                 title(), ingredients(), instructions(), etc.

    Returns:
        CookLang formatted recipe string
    """
    lines = []

    # YAML front matter
    lines.append('---')

    # Title
    title = scraper.title()
    if title:
        lines.append(f'title: {title}')

    # Source URL
    try:
        source = scraper.canonical_url()
        if source:
            lines.append(f'source: {source}')
    except:
        pass

    # Image
    try:
        image = scraper.image()
        if image:
            lines.append(f'image: {image}')
    except:
        pass

    # Servings/Yields
    try:
        yields = scraper.yields()
        if yields:
            lines.append(f'servings: {yields}')
    except:
        pass

    # Times
    try:
        prep_time = scraper.prep_time()
        if prep_time:
            lines.append(f'prep time: {format_time_for_yaml(prep_time)}')
    except:
        pass

    try:
        cook_time = scraper.cook_time()
        if cook_time:
            lines.append(f'cook time: {format_time_for_yaml(cook_time)}')
    except:
        pass

    try:
        total_time = scraper.total_time()
        if total_time:
            lines.append(f'time required: {format_time_for_yaml(total_time)}')
    except:
        pass

    # Nutrition (if available)
    try:
        nutrition = scraper.nutrients()
        if nutrition:
            lines.append('nutrition:')
            for key, value in nutrition.items():
                if value:
                    lines.append(f'  {key}: {value}')
    except:
        pass

    lines.append('---')
    lines.append('')

    # Instructions section
    lines.append('== Instructions ==')
    lines.append('')

    # Get ingredients for reference
    ingredients = []
    try:
        ingredients = scraper.ingredients()
    except:
        pass

    # Parse all ingredients
    parsed_ingredients = []
    for ing in ingredients:
        name, amount, unit = parse_ingredient(ing)
        parsed_ingredients.append({
            'original': ing,
            'name': name,
            'amount': amount,
            'unit': unit,
            'cooklang': format_cooklang_ingredient(name, amount, unit)
        })

    # Get instructions
    instructions = []
    try:
        instructions = scraper.instructions_list()
    except:
        try:
            # Fallback to single instructions string
            instr_text = scraper.instructions()
            if instr_text:
                instructions = [s.strip() for s in instr_text.split('\n') if s.strip()]
        except:
            pass

    # Process instructions - try to link ingredients
    for instruction in instructions:
        processed = instruction

        # Try to replace ingredient mentions with CookLang syntax
        for ing in parsed_ingredients:
            name = ing['name']
            if len(name) > 2:  # Avoid very short names
                # Look for the ingredient name in the instruction
                pattern = rf'\b{re.escape(name)}\b'
                if re.search(pattern, processed, re.IGNORECASE):
                    # Replace first occurrence with CookLang syntax
                    processed = re.sub(
                        pattern,
                        ing['cooklang'],
                        processed,
                        count=1,
                        flags=re.IGNORECASE
                    )

        lines.append(processed)
        lines.append('')

    # If no instructions linked ingredients, add ingredient list at the top
    # Check if any CookLang ingredient syntax was added
    content = '\n'.join(lines)
    if '@' not in content and parsed_ingredients:
        # Insert ingredients section after front matter
        insert_idx = lines.index('== Instructions ==')
        ingredient_lines = ['== Ingredients ==', '']
        for ing in parsed_ingredients:
            ingredient_lines.append(f"- {ing['cooklang']}")
        ingredient_lines.append('')
        lines = lines[:insert_idx] + ingredient_lines + lines[insert_idx:]

    return '\n'.join(lines)


def sanitize_filename(title: str) -> str:
    """Convert recipe title to safe filename."""
    # Remove or replace problematic characters
    filename = title
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)  # Remove illegal chars
    filename = re.sub(r'\s+', ' ', filename)  # Normalize whitespace
    filename = filename.strip()

    # Truncate if too long
    if len(filename) > 100:
        filename = filename[:100].rsplit(' ', 1)[0]

    return filename


if __name__ == '__main__':
    # Test the converter
    import sys
    from recipe_scrapers import scrape_me

    if len(sys.argv) > 1:
        url = sys.argv[1]
        scraper = scrape_me(url)
        print(convert_to_cooklang(scraper))
    else:
        print("Usage: python cooklang_converter.py <recipe-url>")

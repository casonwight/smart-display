#!/usr/bin/env python3
"""Recipe Import Webhook Server.

A simple Flask server that receives recipe URLs and converts them to CookLang format.
Designed to be called from an iOS Shortcut via the Share Sheet.

Usage:
    python recipe_server.py

Endpoints:
    GET  /api/categories  - List available recipe categories
    POST /api/recipe      - Import a recipe from URL

Example:
    curl -X POST http://localhost:5050/api/recipe \
      -H "Content-Type: application/json" \
      -d '{"url": "https://allrecipes.com/recipe/...", "category": "Desserts"}'
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, request, jsonify
from recipe_scrapers import scrape_me
from recipe_scrapers._exceptions import WebsiteNotImplementedError

from cooklang_converter import convert_to_cooklang, sanitize_filename

app = Flask(__name__)

# Configuration
RECIPE_DIR = Path(__file__).parent.parent / 'recipes'
DEFAULT_CATEGORY = 'Uncategorized'
PORT = 5050


def get_categories() -> list[str]:
    """Get list of recipe category folders."""
    categories = []
    if RECIPE_DIR.exists():
        for item in sorted(RECIPE_DIR.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                categories.append(item.name)
    return categories


def ensure_category_exists(category: str) -> Path:
    """Ensure the category folder exists, create if needed."""
    category_path = RECIPE_DIR / category
    category_path.mkdir(parents=True, exist_ok=True)
    return category_path


def get_unique_filename(category_path: Path, base_name: str) -> str:
    """Get a unique filename, appending number if file exists."""
    filename = f"{base_name}.cook"
    filepath = category_path / filename

    if not filepath.exists():
        return filename

    # Append number to make unique
    counter = 2
    while True:
        filename = f"{base_name} ({counter}).cook"
        filepath = category_path / filename
        if not filepath.exists():
            return filename
        counter += 1


@app.route('/api/categories', methods=['GET'])
def list_categories():
    """List available recipe categories."""
    categories = get_categories()
    return jsonify({
        'success': True,
        'categories': categories
    })


@app.route('/api/recipe', methods=['POST'])
def import_recipe():
    """Import a recipe from URL.

    Request body:
        {
            "url": "https://...",
            "category": "Main Dishes"  (optional, defaults to Uncategorized)
        }

    Response:
        {
            "success": true,
            "recipe": "Recipe Title",
            "category": "Main Dishes",
            "path": "recipes/Main Dishes/Recipe Title.cook"
        }
    """
    try:
        data = request.get_json()

        if not data or 'url' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing URL in request body'
            }), 400

        url = data['url'].strip()
        category = data.get('category', DEFAULT_CATEGORY).strip()

        # Validate category
        existing_categories = get_categories()
        if category not in existing_categories and category != DEFAULT_CATEGORY:
            # Check case-insensitive match
            matched = None
            for cat in existing_categories:
                if cat.lower() == category.lower():
                    matched = cat
                    break
            if matched:
                category = matched
            else:
                # Create new category
                pass

        # Scrape the recipe
        print(f"[Recipe Server] Scraping: {url}")
        try:
            scraper = scrape_me(url)
        except WebsiteNotImplementedError:
            return jsonify({
                'success': False,
                'error': f'Website not supported'
            }), 400
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Failed to scrape recipe: {str(e)}'
            }), 400

        # Get title
        title = scraper.title()
        if not title:
            return jsonify({
                'success': False,
                'error': 'Could not extract recipe title'
            }), 400

        print(f"[Recipe Server] Found recipe: {title}")

        # Convert to CookLang
        cooklang_content = convert_to_cooklang(scraper)

        # Ensure category folder exists
        category_path = ensure_category_exists(category)

        # Get safe filename
        safe_name = sanitize_filename(title)
        filename = get_unique_filename(category_path, safe_name)

        # Save the recipe
        filepath = category_path / filename
        filepath.write_text(cooklang_content, encoding='utf-8')

        print(f"[Recipe Server] Saved to: {filepath}")

        return jsonify({
            'success': True,
            'recipe': title,
            'category': category,
            'path': str(filepath.relative_to(RECIPE_DIR.parent))
        })

    except Exception as e:
        print(f"[Recipe Server] Error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/', methods=['GET'])
def index():
    """Simple status page."""
    return jsonify({
        'service': 'Recipe Import Server',
        'status': 'running',
        'endpoints': {
            'GET /api/categories': 'List recipe categories',
            'POST /api/recipe': 'Import recipe from URL'
        }
    })


if __name__ == '__main__':
    # Ensure recipe directory exists
    RECIPE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Recipe Import Server starting on port {PORT}")
    print(f"Recipe directory: {RECIPE_DIR}")
    print(f"Categories: {get_categories()}")
    print()

    # Run on all interfaces so it's accessible from phone
    app.run(host='0.0.0.0', port=PORT, debug=False)

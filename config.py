"""
Application-level constants.
User settings (watched folder, API key, OCR thresholds, theme) are stored
in the `settings` table in the database — not here.
"""

import os
import sys
import pytesseract

# Tesseract binary path (Windows)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Base directory: when frozen by PyInstaller use the exe's folder so that
# the database and log files land next to the executable (writable location).
# In normal development use __file__ as before.
if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.dirname(__file__)

# Path to the SQLite database file (auto-created on first run)
DB_PATH = os.path.join(_BASE, "database", "magazine_library.db")

# AI usage log file
AI_USAGE_LOG = os.path.join(_BASE, "ai_usage.log")

# Import progress log (overwritten each run — open during import to watch live)
IMPORT_LOG = os.path.join(_BASE, "import.log")

# Default OCR settings (overridden by DB settings if present)
DEFAULT_OCR_LANGUAGE = "eng"
DEFAULT_OCR_DPI = 300
DEFAULT_OCR_CONFIDENCE_THRESHOLD = 70

# Claude model to use for AI fallback (always haiku — lowest cost)
AI_MODEL = "claude-haiku-4-5-20251001"

# PDF reader page cache size
PAGE_CACHE_SIZE = 5

# Cover thumbnail DPI
COVER_THUMBNAIL_DPI = 150

# Woodcarving Illustrated master index CSV (dropped in project root)
WCI_INDEX_CSV = os.path.join(os.path.dirname(__file__), "woodcarving_illustrated_article_index.csv")

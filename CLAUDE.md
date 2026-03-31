# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Windows desktop application that manages a personal library of scanned woodworking/wildfowl carving magazines in PDF format. Users configure a watched folder and manually trigger a scan. The app extracts article metadata from each magazine's table of contents (via OCR with AI fallback), stores everything in a local SQLite database, and provides a full-featured PDF reader with search, read-tracking, and star ratings.

**Scanning is always manual** — the app never monitors the folder automatically.

## Tech Stack

| Layer | Technology |
|---|---|
| GUI | PyQt6 |
| PDF Rendering | PyMuPDF (`fitz`) |
| OCR | Tesseract 5 via `pytesseract` |
| AI Fallback | Anthropic Python SDK (`claude-haiku-4-5-20251001`) |
| Database | SQLite via SQLAlchemy ORM + FTS5 full-text search |

## Installation

```bash
pip install PyQt6 PyMuPDF pytesseract sqlalchemy anthropic pillow
```

Tesseract binary must be installed separately (Windows): https://github.com/UB-Mannheim/tesseract/wiki

## Running the App

```bash
python main.py
```

## Verifying Individual Phases

```bash
# Phase 1 — DB init
python -c "from database.db import init_db; init_db()"

# Phase 2 — TOC parser (standalone)
python -m core.toc_parser /path/to/test.pdf

# Phase 3 — Scanner (standalone)
python -m core.scanner /path/to/magazine/folder
```

## Directory Structure

```
magazine_library/
├── main.py                   # Entry point — QApplication + MainWindow
├── config.py                 # App-level constants (not user settings — those live in DB)
├── requirements.txt
├── database/
│   ├── models.py             # SQLAlchemy ORM: Magazine, Article, Setting
│   ├── db.py                 # Engine creation, schema init, FTS5 table + triggers
│   └── magazine_library.db   # Auto-created on first run (gitignored)
├── core/
│   ├── scanner.py            # scan_directory() + import_magazine() pipeline
│   ├── ocr_engine.py         # PyMuPDF render → Tesseract → (text, confidence)
│   ├── ai_extractor.py       # Anthropic API fallback, base64 image, JSON parse
│   └── toc_parser.py         # Text-layer detection, TOC page finder, regex extraction
└── ui/
    ├── main_window.py        # Two-row toolbar, horizontal splitter layout
    ├── library_panel.py      # QTreeWidget: magazine → issue → article (3 levels)
    ├── reader_panel.py       # PyMuPDF + QScrollArea PDF viewer, zoom, page nav
    ├── search_bar.py         # Debounced FTS5 search, results dropdown, advanced panel
    ├── article_detail.py     # Editable metadata panel below library tree
    ├── import_dialog.py      # Modal progress dialog for scan/import
    └── settings_dialog.py    # Tabbed settings: General | OCR & AI
```

## Architecture & Data Flow

### Core Pipeline (triggered by "Scan Now")

1. `scanner.scan_directory()` diffs the filesystem against `magazines.pdf_path` in DB → returns new PDF paths
2. For each new PDF, `scanner.import_magazine()` runs:
   - `toc_parser` detects text layer vs. image scan, finds TOC pages, extracts via regex
   - If OCR confidence < threshold → `ai_extractor` sends base64 page image to Claude Haiku → parses JSON
   - Writes `Magazine` + `Article` records to DB; FTS5 triggers update the search index automatically
   - Renders page 0 at 150 DPI → stores as PNG bytes in `magazines.cover_image`
3. Progress signals emitted to `ImportDialog` throughout

### UI Layout

- `MainWindow` owns a `QSplitter` (horizontal): left = `LibraryPanel` + `ArticleDetail`, right = `ReaderPanel`
- Two-row toolbar: Row 1 = folder path + Browse + Scan Now; Row 2 = search box + Settings
- Selecting an article in `LibraryPanel` → `ReaderPanel` opens the correct PDF and jumps to `page_start`

### Settings Storage

All user settings (watched folder path, API key, OCR thresholds, theme) live in the `settings` table (key/value). There is no config file — read/write via the `Setting` ORM model.

### Search

FTS5 virtual table `articles_fts` mirrors the `articles` table via three SQL triggers (insert/update/delete). Search queries use `articles_fts MATCH ?` with a JOIN back to `articles` and `magazines`. Results debounce 300ms in `SearchBar`.

## Key Implementation Details

### PyMuPDF Page Rendering → QPixmap
```python
import fitz
doc = fitz.open(pdf_path)
pix = doc[page_num].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import QByteArray
pixmap = QPixmap()
pixmap.loadFromData(QByteArray(pix.tobytes("png")), "PNG")
```

### OCR with Confidence Score
```python
import pytesseract
data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
confidences = [int(c) for c in data['conf'] if int(c) > 0]
mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
```

### AI Fallback — Claude Haiku Model ID
Always use `model="claude-haiku-4-5-20251001"`. The TOC prompt instructs the model to return **only** a JSON array (no markdown, no code fences) with keys `title`, `author`, `page`. Log token usage to `ai_usage.log`.

### TOC Regex Patterns
```python
TOC_PATTERNS = [
    r'^(.+?)\s*[.\-]{3,}\s*(\d+)\s*$',           # "Title ........ 42"
    r'^(.+?)\s{2,}([A-Z][a-z].+?)\s{2,}(\d+)$',  # "Title  Author  42"
    r'^(\d+)\s{2,}(.+?)(?:\s{2,}(.+?))?$',        # "42  Title"
]
```

### AI Cost Controls
- Only called when OCR confidence < `ocr_confidence_threshold` setting (default 70)
- Always `claude-haiku-4-5-20251001` — never a more expensive model
- If `ask_before_ai` setting is true (default), show confirmation dialog before each API call
- Result cached in DB — never re-send the same TOC page to AI

## Build Order (Phases)

Build phases in sequence — each is independently testable before the next:

1. **Database** — `models.py`, `db.py` (schema + FTS5 triggers)
2. **PDF/OCR Core** — `ocr_engine.py`, `toc_parser.py`, `ai_extractor.py`
3. **Scanner** — `scanner.py` (ties core modules together, progress callbacks)
4. **GUI Shell** — `main_window.py`, `settings_dialog.py` (toolbar, splitter, Browse wired to DB)
5. **PDF Reader** — `reader_panel.py` (render, zoom, nav, 5-page cache)
6. **Library Panel** — `library_panel.py`, `article_detail.py` (tree + metadata editor)
7. **Search** — `search_bar.py` (FTS5, debounce, dropdown, advanced panel)
8. **Polish** — `import_dialog.py` with progress/AI prompts, right-click menus, cover thumbnails, first-run UX

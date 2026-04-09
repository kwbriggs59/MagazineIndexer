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
| OCR | Tesseract 5 via `pytesseract` (only needed for image-only PDFs) |
| AI Fallback | Anthropic Python SDK (`claude-haiku-4-5-20251001`) |
| Database | SQLite via SQLAlchemy ORM + FTS5 full-text search |

## Installation

```bash
pip install PyQt6 PyMuPDF pytesseract sqlalchemy anthropic pillow
```

Tesseract binary must be installed separately (Windows): https://github.com/UB-Mannheim/tesseract/wiki

**Tesseract is only required if importing image-only PDFs** (no text layer). If all PDFs have been pre-OCR'd, Tesseract is never called during import, though `pytesseract` is still imported at startup.

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
MagazineIndexer/
├── main.py                        # Entry point — QApplication + MainWindow
├── config.py                      # App-level constants (not user settings — those live in DB)
├── requirements.txt
├── woodcarving_illustrated_article_index.csv  # WCI master index data (used by WciIndexPanel)
├── database/
│   ├── models.py                  # SQLAlchemy ORM: Magazine, Article, Setting
│   ├── db.py                      # Engine creation, schema init, FTS5 table + triggers
│   └── magazine_library.db        # Auto-created on first run (gitignored)
├── core/
│   ├── scanner.py                 # scan_directory(), import_magazine(), import_index() pipeline
│   ├── ocr_engine.py              # PyMuPDF render → Tesseract → (text, confidence)
│   ├── ai_extractor.py            # Anthropic API fallback, base64 image, JSON parse
│   ├── toc_parser.py              # Text-layer detection, TOC page finder, regex extraction
│   └── wfc_index_parser.py        # Parses Wildfowl Carving master index PDF → article dicts
└── ui/
    ├── main_window.py             # Two-row toolbar, QStackedWidget central layout
    ├── magazine_grid.py           # Home screen: cover thumbnail grid + pub/ownership filters
    ├── toc_panel.py               # Single-issue TOC list shown in reader mode
    ├── library_panel.py           # RETIRED from main flow — kept for reference
    ├── reader_panel.py            # PyMuPDF + QScrollArea PDF viewer, zoom, nav, grab/pan
    ├── search_bar.py              # Debounced FTS5 search, popup results (Qt.WindowType.Popup)
    ├── article_detail.py          # Editable metadata panel — embedded in TocPanel below the article list
    ├── import_dialog.py           # Modal progress dialog + file logging (import.log)
    ├── settings_dialog.py         # Tabbed settings: General | OCR & AI
    └── wci_index_panel.py         # Non-modal WCI issue browser with ownership tracking
```

## Architecture & Data Flow

### Core Pipeline (triggered by "Scan Now")

1. `scanner.scan_directory()` diffs the filesystem against `magazines.pdf_path` in DB → returns new PDF paths
2. For each new PDF, `scanner.import_magazine()` runs:
   - `toc_parser` detects text layer vs. image scan, finds TOC pages, extracts via regex
   - `_detect_page_offset()` scans pages after the TOC to find the first printed page number, computes `offset = pdf_index - printed_num + 1`
   - If season missing from filename, `_detect_season_from_pdf()` scans first 6 pages for Winter/Spring/Summer/Fall
   - If OCR confidence < threshold → `ai_extractor` sends base64 page image to Claude Haiku → parses JSON
   - Writes `Magazine` + `Article` records to DB; FTS5 triggers update the search index automatically
   - Renders page 0 at 150 DPI → stores as PNG bytes in `magazines.cover_image`
   - `_reconcile_with_index()` fills `page_start` on any virtual catalog records that match by publication + season + year
3. Progress signals emitted to `ImportDialog` throughout; all output also written to `import.log`

### Re-import and Delete

- Right-click a magazine card in the grid → "Re-import Issue…" or "Delete Issue…"
- **Re-import**: deletes the existing `Magazine` + `Article` records from DB, then runs the full import pipeline on the same PDF path via `ImportDialog(pdf_paths=[path])`
- **Delete**: confirms with user, calls `session.delete(mag)` — ORM cascade + `ondelete="CASCADE"` on the Article FK handles article cleanup automatically
- `scan_directory()` matches by exact absolute path — updating a PDF file in-place without re-importing has no effect; the scanner sees the path as already known

### Text Layer Detection and Tesseract Usage

`has_text_layer()` checks if any of the first 3 pages contain >50 characters of extractable text (PyMuPDF `get_text()`). If true, **Tesseract is never called** for that PDF — all extraction uses PyMuPDF's built-in text layer. Tesseract is only invoked for image-only scans (no text layer). Most user PDFs have been pre-OCR'd and will never trigger Tesseract.

### Virtual Catalog Records

Master index PDFs (e.g. a multi-year Wildfowl Carving index) can be imported via "Import Index…" without real PDFs:
- Creates `Magazine` records with `pdf_path=NULL` and `extraction_method="index"`
- Articles get `page_start=NULL` — shown in grid as "Not Owned" (no cover image)
- When a real PDF is later scanned, `_reconcile_with_index()` matches by publication + season + year and fills in `page_start`
- `ReaderPanel` shows a "No PDF available — catalog-only entry" message instead of opening a PDF

### UI Layout

`MainWindow` uses a `QStackedWidget` as its central widget with two pages:

**Page 0 — Home screen (`MagazineGrid`):**
- Grid of magazine cover thumbnail cards (5 columns, fixed 165×250px cards)
- Filter bar: "Magazine" combo (publication) + "Owned" combo (All / Owned / Not Owned)
  - Owned = `pdf_path IS NOT NULL`; Not Owned = catalog-only entries with `pdf_path IS NULL`
- Left-click a card → switch to reader mode (page 1)
- Right-click a card → "Re-import Issue…" / "Delete Issue…"

**Page 1 — Reader mode (`QSplitter`):**
- Left: `TocPanel` — "← Back" button, issue label (right-click → "Set Page Offset…"), article list sorted by page number
- Right: `ReaderPanel` — PDF viewer with zoom, page nav, grab/pan
- Clicking an article in `TocPanel` → `ReaderPanel.open_article(article_id)` jumps to that page
- "← Back" → returns to home screen (page 0) and refreshes the grid

**Toolbar (both modes):**
- Row 1: Folder path + Browse + Scan Now
- Row 2: SearchBar + WCI Index + Import Index + Settings
- Search results: clicking an article loads that magazine's TOC into `TocPanel`, opens the article in `ReaderPanel`, and switches to reader mode (page 1)

### PDF Viewer Grab/Pan

`ReaderPanel` installs an event filter on `self._scroll.viewport()`:
- Left mouse button press → `ClosedHandCursor`, record `globalPosition()` + current scroll bar values
- Mouse move while pressed → delta the horizontal and vertical scroll bars
- Release → `OpenHandCursor`
- Use `event.globalPosition().toPoint()` (PyQt6 API) — not `event.globalPos()` (PyQt5)

### Settings Storage

All user settings (watched folder path, API key, OCR thresholds, theme) live in the `settings` table (key/value). There is no config file — read/write via the `Setting` ORM model or `get_setting()`/`set_setting()` helpers in `db.py`.

### Search

FTS5 virtual table `articles_fts` mirrors the `articles` table via three SQL triggers (insert/update/delete). Search queries use `articles_fts MATCH ?` with a JOIN back to `articles` and `magazines`. Results debounce 300ms in `SearchBar`. The results list is a `Qt.WindowType.Popup` widget (parented to None) positioned via `mapToGlobal` — keeps it from being clipped by the toolbar.

Search article selection goes through `MainWindow._on_search_article_selected` (not directly to `ReaderPanel`) so the correct magazine's TOC is loaded and the stack switches to reader mode.

### Page Offset

PDF page indices are 0-based; printed article page numbers are not. Most magazines have several unnumbered front-matter pages. `page_offset` per magazine bridges this:
```
pdf_page_index = article.page_start - 1 + magazine.page_offset
```
Auto-detected on import by `_detect_page_offset()`. Adjustable per-issue via right-click on the issue label in `TocPanel` → "Set Page Offset…". Range is -50 to 50 (negative offsets are valid when printed page numbers start before PDF page 1).

### Portability

| Item | Portable? | Notes |
|---|---|---|
| Project folder (code, DB, logs, CSV) | ✅ Yes | All paths use `os.path.dirname(__file__)` |
| Watched folder path | ⚠️ Reset needed | Stored as absolute path in `settings` table — click Browse after moving |
| PDF paths in `magazines` table | ⚠️ Reset needed | Stored as absolute paths — re-scan or SQL `REPLACE()` if drive/username changes |
| Tesseract binary path | ⚠️ Hardcoded | `config.py` line 11 — must match install location on new machine |
| Anthropic API key | ⚠️ Re-enter | Stored in `settings` table |

To remap PDF paths after moving: `UPDATE magazines SET pdf_path = REPLACE(pdf_path, 'C:\old\path', 'C:\new\path')`

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

### Multi-line Author Extraction (`toc_parser.py`)
Two TOC author formats are supported via `_find_author_after(lines, start)`:
- **Wildfowl Carving style**: plain author name on the immediately-next non-empty line after the title
- **WCI style**: `By Author Name` anywhere within the next 10 lines

```python
_BY_AUTHOR_RE = re.compile(r"^[Bb]y\s+(.+)$")

def _looks_like_author(line):
    # not digit start, no digits, 1-6 words, ≤60 chars, not ALL-CAPS
    ...
```

### Session Management
- `sessionmaker(expire_on_commit=False)` — column values remain accessible after `commit()` + `session.close()`
- `PRAGMA foreign_keys=ON` enforced per connection via `@event.listens_for(_engine, "connect")`
- Prefer `session.delete(mag)` over bulk `session.query(Magazine).delete()` — the latter bypasses ORM cascade

### AI Cost Controls
- Only called when OCR confidence < `ocr_confidence_threshold` setting (default 70)
- Always `claude-haiku-4-5-20251001` — never a more expensive model
- If `ask_before_ai` setting is true (default), show confirmation dialog before each API call
- Result cached in DB — never re-send the same TOC page to AI

### Magazine Card Click Area
Child `QLabel` widgets inside `_MagazineCard` must have `setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)` so clicks pass through to the card's `mousePressEvent`. Without this, clicks on the cover image or labels don't register.

### ImportDialog — Single-File Re-import
`ImportDialog` accepts an optional `pdf_paths` list. When provided, the worker skips `scan_directory()` and imports exactly those files. Used by the re-import flow: delete DB record first, then open `ImportDialog(folder, pdf_paths=[path])`.

## Build Order (Phases)

1. ✅ **Database** — `models.py`, `db.py` (schema + FTS5 triggers + migrations)
2. ✅ **PDF/OCR Core** — `ocr_engine.py`, `toc_parser.py`, `ai_extractor.py`
3. ✅ **Scanner** — `scanner.py` (pipeline, page offset detection, season detection, reconciliation, index import)
4. ✅ **GUI Shell** — `main_window.py`, `settings_dialog.py`
5. ✅ **PDF Reader** — `reader_panel.py` (render, zoom, nav, page cache, page offset, grab/pan)
6. ✅ **Library Panel** — `library_panel.py`, `article_detail.py` (tree + pub filter + metadata editor — retired from main flow)
7. ✅ **Search** — `search_bar.py` (FTS5, debounce, popup results)
8. ✅ **Import Dialog + Polish** — `import_dialog.py` (progress, AI prompts, file logging, single-file re-import)
9. ✅ **Index Import** — `wfc_index_parser.py`, `import_index()`, virtual catalog records, reconciliation
10. ✅ **WCI Index Panel** — `wci_index_panel.py`, ownership tracking, CSV-based issue browser
11. ✅ **Magazine Grid + Reader Mode** — `magazine_grid.py`, `toc_panel.py`, redesigned `main_window.py` (QStackedWidget, home/reader modes, re-import/delete from grid)

## Lessons Learned from Phase 2 Testing

Tested against two PDFs:
- `2015-3 Wildfowl Carving.pdf` — pure image scan, two-column TOC
- `WCI99 Issue.pdf` — searchable text layer, single-column TOC with sidebar quick-index

**TOC detection fixes (`toc_parser.py`):**
- Keyword match must be line-level (not full-text) — "Contents copyright" in mastheads was a false positive
- Heuristic regex must require `^[A-Z]` at line start — filters phone numbers, prices, masthead lines
- Do NOT lowercase text before passing to `_is_toc_page()` — breaks the uppercase regex
- Many Wildfowl Carving TOC magazines have two-column layout; OCR garbles dot leaders into noise strings

**False positive deduplication (`toc_parser.py`):**
- Some magazines have a sidebar "quick index" (e.g. `Bird...23`, `Whimsey...43`) alongside the main TOC
- These echo real articles with shorter/category labels and must be filtered
- Strategy: track `_pattern` per parsed entry; after collecting all pages, build `page_first_pages` from Pattern C (page-number-first) matches; discard Pattern A/D/E entries that share a page number
- Build `page_first_pages` BEFORE applying `EXCLUDED_HEADINGS` so department pages (e.g. `96  Woodchips`) still claim their page number, blocking sidebar echoes at that page
- `EXCLUDED_HEADINGS` checks must be applied to the **extracted title**, not the raw line

**AI extractor fixes (`ai_extractor.py`):**
- Claude Haiku returns ```json code fences despite the prompt saying not to — strip them before `json.loads()`
- Render TOC pages at **150 DPI** (not 300) for AI calls — 300 DPI PNGs exceed the 5MB API image limit

## Lessons Learned from Phase 3–10 Testing

**Import hangs (`import_dialog.py`, `ocr_engine.py`):**
- Tesseract can hang indefinitely on noisy image strips (e.g. page edge artifacts). Always pass `timeout=5` to `pytesseract.image_to_data()`.
- Write import progress to a log file (`import.log`) immediately — the Qt text widget is inaccessible if the app hangs and can't be copied from.

**Duplicate FTS articles / orphan articles:**
- `session.query(Magazine).delete()` bypasses ORM cascade — articles remain as orphans and get re-indexed next import.
- Fix: enable `PRAGMA foreign_keys=ON` (per-connection via SQLAlchemy event) + `ON DELETE CASCADE` on Article FK + orphan cleanup in `_migrate()` + `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')` after cleanup.
- To clear the DB manually: delete articles first (`session.query(Article).delete()`), then magazines.

**Session expiry after commit:**
- SQLAlchemy default: all attributes expire after `commit()`. Accessing `mag.articles` after `session.close()` raises `DetachedInstanceError`.
- Fix: `sessionmaker(expire_on_commit=False)` — column values remain readable after close.

**Search popup clipped by toolbar:**
- A `QListWidget` inside a `QToolBar` is clipped at the toolbar boundary. Make the results widget a `Qt.WindowType.Popup` parented to `None`, positioned via `self._search_input.mapToGlobal(rect().bottomLeft())`.

**Qt stylesheet `cursor` property:**
- Qt stylesheets do not support CSS `cursor: pointer`. Use `widget.setCursor(Qt.CursorShape.PointingHandCursor)` instead. Setting it in a stylesheet emits "Unknown property cursor" warnings × N widgets at startup.

**Author extraction:**
- Watched-folder Wildfowl Carving PDFs have a pre-existing text layer (OCR'd by prior software), unlike the test PDFs. Both have articles without inline authors.
- Wildfowl Carving: author appears on the line immediately after the title.
- WCI: author appears as "By Name" anywhere in the next ~10 lines.
- `_find_author_after()` in `toc_parser.py` handles both formats with a 10-line lookahead.

**Season detection:**
- Filenames like `2012-2 Wildfowl Carving.pdf` contain no season. Call `_detect_season_from_pdf()` (scans first 6 pages for Winter/Spring/Summer/Fall) when the filename yields no season — needed for reconciliation matching.

**SQLite nullable column migration:**
- SQLite cannot `ALTER TABLE` to remove a NOT NULL constraint. Pattern: `CREATE TABLE _new` with desired schema → `INSERT INTO _new SELECT ...` → `DROP TABLE old` → `ALTER TABLE _new RENAME TO old`. Must disable FK enforcement (`PRAGMA foreign_keys=OFF`) during the swap.

**PyQt6 mouse event API:**
- Use `event.globalPosition().toPoint()` for global mouse coordinates — `event.globalPos()` is the PyQt5 API and raises `AttributeError` in PyQt6.

**Environment:**
- Python is in Miniconda: `/c/Users/kwbri/.conda/envs/mag/python.exe`
- Tesseract installed at: `C:\Program Files\Tesseract-OCR\tesseract.exe` (set in `config.py`) — only needed for image-only PDFs
- Run all commands with the full conda env path or from within the activated `mag` environment

## Pi Web Server (MagazineServer)

A companion Flask app at `/home/kevin/MagazineServer` on a Raspberry Pi at `192.168.0.30` (port 5000). Serves the library to any browser on the local network. Managed as a systemd service (`magazine-server.service`).

The DB lives on a Google Drive rclone mount at `/mnt/gdrive/magazine_library.db`. The service requires `rclone-gdrive.service` to be running first.

### DB Sync Workflow

1. In the desktop app: **Settings → Remote Database → Sync Local → Remote**
   - Merges any server-side edits (ratings, read status, notes) back into the local DB first
   - Uses `sqlite3.backup()` (not `shutil.copy2`) to copy atomically — safe while the server is running
2. After syncing, restart the Pi server to pick up new data and re-prime the cover cache:
   ```
   ssh kevin@192.168.0.30 "sudo systemctl restart magazine-server"
   ```

You do **not** need to stop the server before syncing — SQLite's backup API and WAL mode handle concurrent access safely. But a restart afterward is required so SQLAlchemy's connection pool reconnects to the updated DB and the cover cache priming thread runs for any new magazines.

### Cover Cache

Covers (PNG blobs in `magazines.cover_image`) are extracted from the DB at startup and cached to `/home/kevin/MagazineServer/cover_cache/<id>.png` on the Pi's local disk by a background daemon thread. Subsequent requests serve from local disk, bypassing the slow rclone mount entirely.

- Cache persists across restarts — only new/missing covers are written each time
- After a DB sync with new magazines, restart the server to prime the new covers
- If covers look stale or wrong: `rm /home/kevin/MagazineServer/cover_cache/*.png` then restart

### Troubleshooting

- **"database disk image is malformed"**: stale `.db-shm` file left by an unclean shutdown. Delete `/mnt/gdrive/magazine_library.db-shm` then restart.
- **Port 5000 already in use after crash**: `ssh kevin@192.168.0.30 "sudo fuser -k 5000/tcp"` then restart.
- **Logs**: `journalctl -u magazine-server -n 50 --no-pager`

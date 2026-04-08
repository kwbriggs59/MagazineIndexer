"""
Directory scanner and magazine import pipeline.

Triggered only when the user clicks "Scan Now" — never runs automatically.

Public API:
    scan_directory(folder_path) -> list[str]   # new PDF paths not yet in DB
    import_magazine(pdf_path, progress_callback, settings) -> Magazine

Run standalone for testing:
    python -m core.scanner /path/to/magazine/folder
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from io import BytesIO

import config
from database.db import get_session
from database.models import Magazine, Article
from core.toc_parser import parse_toc, has_text_layer, find_toc_pages
from core.ocr_engine import render_page_to_image
from core.ai_extractor import extract_toc_with_ai
from core.wfc_index_parser import parse_wfc_index


def scan_directory(folder_path: str) -> list[str]:
    """
    Walk folder_path recursively and return PDF paths not yet in the database.

    Args:
        folder_path: Absolute path to the watched magazine folder.

    Returns:
        List of absolute PDF file paths that are new (not in magazines.pdf_path).
    """
    session = get_session()
    try:
        known_paths = {row[0] for row in session.query(Magazine.pdf_path).all()}
    finally:
        session.close()

    new_paths = []
    for root, _dirs, files in os.walk(folder_path):
        for fname in files:
            if fname.lower().endswith(".pdf"):
                abs_path = os.path.abspath(os.path.join(root, fname))
                if abs_path not in known_paths:
                    new_paths.append(abs_path)

    return sorted(new_paths)


def _extract_cover_thumbnail(pdf_path: str) -> bytes:
    """Render the cover page at COVER_THUMBNAIL_DPI and return PNG bytes."""
    image = render_page_to_image(pdf_path, 0, dpi=config.COVER_THUMBNAIL_DPI)
    from io import BytesIO
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _guess_metadata_from_filename(pdf_path: str) -> dict:
    """
    Attempt to extract title, volume, issue number, season, year, and publication
    from the file name. Returns a dict with any fields that could be identified.

    Example filenames this handles:
        2012-2 Wildfowl Carving.pdf          → publication="Wildfowl Carving"
        WCI99 Issue.pdf                      → publication="Woodcarving Illustrated"
        WildfowlCarving_Vol5_No2_Spring2003  → publication="Wildfowl Carving"
    """
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    meta: dict = {"title": name, "volume": None, "issue_number": None,
                  "season": None, "year": None, "publication": None}

    if m := re.search(r"[Vv]ol(?:ume)?[\s_-]*(\d+)", name):
        meta["volume"] = int(m.group(1))

    if m := re.search(
        r"[Nn]o\.?[\s_-]*(\d+)"        # No. 12 / No12
        r"|[Ii]ssue[\s_-]*(\d+)"        # Issue 12 / Issue12
        r"|[Ww][Cc][Ii][\s_-]?(\d+)",   # WCI99 / wci 99
        name,
    ):
        meta["issue_number"] = int(m.group(1) or m.group(2) or m.group(3))

    for season in ("Spring", "Summer", "Fall", "Autumn", "Winter"):
        if season.lower() in name.lower():
            meta["season"] = season
            break

    if m := re.search(r"(19|20)\d{2}", name):
        meta["year"] = int(m.group(0))

    # --- Publication detection ---
    # WCI prefix → Woodcarving Illustrated
    if re.match(r"[Ww][Cc][Ii]\d", name) or re.match(r"[Ww][Cc][Ii]\s", name):
        meta["publication"] = "Woodcarving Illustrated"
    # "Wildfowl" anywhere in name → Wildfowl Carving
    elif re.search(r"[Ww]ildfowl", name):
        meta["publication"] = "Wildfowl Carving"
    else:
        # Strip leading "YYYY-N " date prefix to get the publication name
        # e.g. "2012-2 Wildfowl Carving" → "Wildfowl Carving"
        cleaned = re.sub(r"^\d{4}-\d+\s+", "", name).strip()
        # Replace underscores with spaces and strip trailing parentheticals/numbers
        cleaned = cleaned.replace("_", " ")
        cleaned = re.sub(r"\s*\(\d+\)\s*$", "", cleaned).strip()  # e.g. "(2)" suffix
        if cleaned and cleaned != name.replace("_", " "):
            meta["publication"] = cleaned
        else:
            meta["publication"] = cleaned or name

    return meta


def _auto_keywords(title: str) -> str:
    """Strip common stopwords from an article title and return comma-separated keywords."""
    STOPWORDS = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "up", "about", "into", "through", "how",
        "is", "it", "its", "be", "as", "are", "was", "were",
    }
    words = re.findall(r"[A-Za-z]{3,}", title)
    keywords = [w.lower() for w in words if w.lower() not in STOPWORDS]
    return ", ".join(dict.fromkeys(keywords))  # deduplicated, order-preserved


def _detect_page_offset(
    pdf_path: str,
    toc_pages: list[int],
    has_text: bool,
    dpi: int = 100,
) -> int:
    """
    Scan pages after the TOC to find the first printed page number, then
    return page_offset = (PDF page index) - (printed page number) + 1.

    This accounts for unnumbered pages (cover, inside cover, ads) that appear
    before the magazine's page 1.  Returns 0 if detection fails.
    """
    # Page number regex: a standalone 1-3 digit integer, not part of a longer number
    _PAGE_NUM_RE = re.compile(r'(?<!\d)(\d{1,3})(?!\d)')

    doc = fitz.open(pdf_path)
    try:
        start = max(1, max(toc_pages) + 1) if toc_pages else 1
        limit = min(start + 10, doc.page_count)

        for i in range(start, limit):
            page = doc[i]
            h = page.rect.height
            w = page.rect.width
            found_num = None

            if has_text:
                # Text layer: look for standalone integers in the top/bottom 12%
                for word in page.get_text("words"):
                    # word = (x0, y0, x1, y1, text, block, line, word_index)
                    x0, y0, x1, y1, text = word[:5]
                    if y1 < h * 0.12 or y0 > h * 0.88:
                        text = text.strip()
                        if re.fullmatch(r'\d{1,3}', text):
                            candidate = int(text)
                            if 1 <= candidate <= 100:
                                found_num = candidate
                                break
            else:
                # Image scan: OCR just the top and bottom strips (fast)
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                for clip in [
                    fitz.Rect(0, 0,        w, h * 0.12),   # top strip
                    fitz.Rect(0, h * 0.88, w, h),           # bottom strip
                ]:
                    pix = page.get_pixmap(matrix=mat, clip=clip)
                    img = Image.open(BytesIO(pix.tobytes("png")))
                    text = pytesseract.image_to_string(img, config="--psm 6 --oem 1 -c tessedit_do_invert=0", timeout=5)
                    for tok in _PAGE_NUM_RE.findall(text):
                        candidate = int(tok)
                        if 1 <= candidate <= 100:
                            found_num = candidate
                            break
                    if found_num is not None:
                        break

            if found_num is not None:
                offset = i - found_num + 1
                if offset >= 0:
                    return offset

    except Exception:
        pass
    finally:
        doc.close()

    return 0


def import_magazine(
    pdf_path: str,
    progress_callback: Optional[Callable[[str], None]] = None,
    ocr_dpi: int = None,
    confidence_threshold: int = None,
    api_key: str = None,
    ask_before_ai: bool = True,
    ai_confirm_callback: Optional[Callable[[str], bool]] = None,
) -> Magazine:
    """
    Full import pipeline for one PDF file.

    Args:
        pdf_path:             Absolute path to the PDF.
        progress_callback:    Called with a status string at each major step.
        ocr_dpi:              OCR resolution. Defaults to config.DEFAULT_OCR_DPI.
        confidence_threshold: Min OCR confidence before AI fallback.
        api_key:              Anthropic API key (required for AI fallback).
        ask_before_ai:        If True, call ai_confirm_callback before using AI.
        ai_confirm_callback:  Called with filename; should return True to proceed.

    Returns:
        The newly created Magazine ORM object (already committed to DB).

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        ValueError:        If the PDF is already in the database.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if ocr_dpi is None:
        ocr_dpi = config.DEFAULT_OCR_DPI
    if confidence_threshold is None:
        confidence_threshold = config.DEFAULT_OCR_CONFIDENCE_THRESHOLD

    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    _progress(f"Opening {os.path.basename(pdf_path)}…")

    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    doc.close()

    # --- Metadata ---
    meta = _guess_metadata_from_filename(pdf_path)
    if not meta["season"]:
        meta["season"] = _detect_season_from_pdf(pdf_path)
    _progress("Guessed metadata from filename.")

    # --- TOC extraction ---
    _progress("Detecting text layer…")
    text_layer = has_text_layer(pdf_path)
    articles_data, confidence = parse_toc(pdf_path, dpi=ocr_dpi,
                                          confidence_threshold=confidence_threshold)
    extraction_method = "pdf_text" if text_layer else "ocr"

    # --- Page offset detection ---
    _progress("Detecting page offset…")
    toc_pages = find_toc_pages(pdf_path, text_layer, ocr_dpi)
    page_offset = _detect_page_offset(pdf_path, toc_pages, text_layer)

    # --- AI fallback ---
    if (confidence < confidence_threshold or len(articles_data) < 3) and api_key:
        use_ai = True
        if ask_before_ai and ai_confirm_callback:
            use_ai = ai_confirm_callback(os.path.basename(pdf_path))

        if use_ai:
            _progress("Running AI extraction (Claude Haiku)…")
            if not toc_pages:
                toc_pages = find_toc_pages(pdf_path, text_layer, ocr_dpi)
            if toc_pages:
                # Use 150 DPI for AI — high enough to read, stays under 5MB API limit
                page_image = render_page_to_image(pdf_path, toc_pages[0], dpi=150)
                try:
                    ai_results = extract_toc_with_ai(page_image, api_key)
                    # Normalise AI result keys to match toc_parser output
                    articles_data = [
                        {
                            "title": r.get("title", ""),
                            "author": r.get("author"),
                            "page_number": r.get("page"),
                        }
                        for r in ai_results
                        if r.get("title")
                    ]
                    extraction_method = "ai"
                    _progress(f"AI extracted {len(articles_data)} articles.")
                except Exception as e:
                    _progress(f"AI extraction failed: {e}. Keeping OCR results.")

    # --- Cover thumbnail ---
    _progress("Extracting cover thumbnail…")
    cover_bytes = _extract_cover_thumbnail(pdf_path)

    # --- Write to DB ---
    _progress("Writing to database…")
    session = get_session()
    try:
        magazine = Magazine(
            title=meta["title"],
            volume=meta["volume"],
            issue_number=meta["issue_number"],
            season=meta["season"],
            year=meta["year"],
            publication=meta.get("publication"),
            pdf_path=pdf_path,
            page_count=page_count,
            cover_image=cover_bytes,
            page_offset=page_offset,
        )
        session.add(magazine)
        session.flush()  # get magazine.id before adding articles

        for art in articles_data:
            article = Article(
                magazine_id=magazine.id,
                title=art.get("title", ""),
                author=art.get("author"),
                page_start=art.get("page_number"),
                keywords=_auto_keywords(art.get("title", "")),
                extraction_method=extraction_method,
            )
            session.add(article)

        session.commit()
        article_count = len(articles_data)
        _progress(f"Imported {article_count} articles.")

        # Reconcile with any existing virtual (index-only) records
        _reconcile_with_index(session, magazine, articles_data)

        magazine.article_count = article_count  # transient convenience attr for caller
        return magazine

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def import_book(
    pdf_path: str,
    title: str,
    author: str | None = None,
    extract_toc: bool = True,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Magazine:
    """
    Import a PDF book into the library.

    If extract_toc is True, attempts to extract a table of contents and
    creates Article records for each chapter found. The author parameter is
    used as a fallback author on chapters that don't have one.

    Returns the newly created Magazine ORM object.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    _progress(f"Opening {os.path.basename(pdf_path)}…")
    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    doc.close()

    _progress("Extracting cover thumbnail…")
    cover_bytes = _extract_cover_thumbnail(pdf_path)

    articles_data: list[dict] = []
    page_offset = 0
    extraction_method = "manual"

    if extract_toc:
        _progress("Detecting text layer…")
        text_layer = has_text_layer(pdf_path)
        _progress("Extracting table of contents…")
        articles_data, _ = parse_toc(pdf_path, dpi=config.DEFAULT_OCR_DPI,
                                     confidence_threshold=config.DEFAULT_OCR_CONFIDENCE_THRESHOLD)
        extraction_method = "pdf_text" if text_layer else "ocr"
        if articles_data:
            toc_pages = find_toc_pages(pdf_path, text_layer, config.DEFAULT_OCR_DPI)
            page_offset = _detect_page_offset(pdf_path, toc_pages, text_layer)
        _progress(f"Found {len(articles_data)} chapters.")

    _progress("Writing to database…")
    session = get_session()
    try:
        magazine = Magazine(
            title=title,
            publication=title,      # used as the display label in the grid
            pdf_path=pdf_path,
            page_count=page_count,
            cover_image=cover_bytes,
            page_offset=page_offset,
            content_type="book",
        )
        session.add(magazine)
        session.flush()

        for art in articles_data:
            article = Article(
                magazine_id=magazine.id,
                title=art.get("title", ""),
                author=art.get("author") or author,
                page_start=art.get("page_number"),
                keywords=_auto_keywords(art.get("title", "")),
                extraction_method=extraction_method,
            )
            session.add(article)

        session.commit()
        _progress(f"Imported book with {len(articles_data)} chapters.")
        return magazine

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def import_article(
    pdf_path: str,
    title: str,
    author: str | None = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Magazine:
    """
    Import a single-article PDF into the library.

    Creates one Magazine record (content_type="article") and one Article record
    covering the entire document. The article is immediately openable in the reader.

    Returns the newly created Magazine ORM object.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    _progress(f"Opening {os.path.basename(pdf_path)}…")
    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    doc.close()

    _progress("Extracting cover thumbnail…")
    cover_bytes = _extract_cover_thumbnail(pdf_path)

    _progress("Writing to database…")
    session = get_session()
    try:
        magazine = Magazine(
            title=title,
            publication=title,      # used as the display label in the grid
            pdf_path=pdf_path,
            page_count=page_count,
            cover_image=cover_bytes,
            page_offset=0,
            content_type="article",
        )
        session.add(magazine)
        session.flush()

        article = Article(
            magazine_id=magazine.id,
            title=title,
            author=author,
            page_start=1,
            keywords=_auto_keywords(title),
            extraction_method="manual",
        )
        session.add(article)

        session.commit()
        _progress("Article imported.")
        return magazine

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for fuzzy title matching."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _detect_season_from_pdf(pdf_path: str) -> str | None:
    """
    Scan the first 6 pages of a PDF for season keywords and return the season name,
    or None if not found.
    """
    seasons = ("Winter", "Spring", "Summer", "Fall", "Autumn")
    doc = fitz.open(pdf_path)
    try:
        limit = min(6, doc.page_count)
        for i in range(limit):
            text = doc[i].get_text("text").lower()
            for season in seasons:
                if season.lower() in text:
                    return season if season != "Autumn" else "Fall"
    except Exception:
        pass
    finally:
        doc.close()
    return None


def _reconcile_with_index(session, magazine: Magazine, articles_data: list[dict]):
    """
    After importing a real PDF, find any virtual (index-only) Magazine records
    for the same publication+season+year and fill in page_start for matching articles.
    """
    if not magazine.publication or not magazine.season or not magazine.year:
        return

    try:
        virtual_mags = (
            session.query(Magazine)
            .filter(
                Magazine.publication == magazine.publication,
                Magazine.season == magazine.season,
                Magazine.year == magazine.year,
                Magazine.pdf_path.is_(None),
            )
            .all()
        )

        if not virtual_mags:
            return

        # Build a lookup: normalized title → page_number from the real PDF's articles
        real_lookup: dict[str, int] = {}
        for art in articles_data:
            title = art.get("title", "")
            page_num = art.get("page_number")
            if title and page_num is not None:
                real_lookup[_normalize_title(title)] = page_num

        if not real_lookup:
            return

        updated = 0
        for vmag in virtual_mags:
            for varticle in vmag.articles:
                if varticle.page_start is None:
                    norm = _normalize_title(varticle.title)
                    if norm in real_lookup:
                        varticle.page_start = real_lookup[norm]
                        updated += 1

        if updated:
            session.commit()

    except Exception:
        pass  # reconciliation is best-effort


def import_index(
    index_pdf_path: str,
    publication: str,
    progress_callback=None,
) -> int:
    """
    Import a master index PDF (like Wildfowl_Carving_TOC_All_Issues.pdf).

    Creates virtual Magazine records (pdf_path=None) and Article records
    (page_start=None, extraction_method="index") for issues not already in the DB.

    Returns the number of new articles created.
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    _progress(f"Parsing index PDF: {os.path.basename(index_pdf_path)}…")
    entries = parse_wfc_index(index_pdf_path)
    _progress(f"Found {len(entries)} articles in index.")

    # Group by (season, year)
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for entry in entries:
        groups[(entry["season"], entry["year"])].append(entry)

    session = get_session()
    total_new_articles = 0

    try:
        for (season, year), articles in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
            _progress(f"Processing {season} {year} ({len(articles)} articles)…")

            # Check if a real PDF record already exists for this issue
            real_mag = (
                session.query(Magazine)
                .filter(
                    Magazine.publication == publication,
                    Magazine.season == season,
                    Magazine.year == year,
                    Magazine.pdf_path.isnot(None),
                )
                .first()
            )

            # Check if a virtual record already exists
            virtual_mag = (
                session.query(Magazine)
                .filter(
                    Magazine.publication == publication,
                    Magazine.season == season,
                    Magazine.year == year,
                    Magazine.pdf_path.is_(None),
                )
                .first()
            )

            if real_mag:
                # Real PDF exists — try to reconcile page numbers for index articles
                real_articles_data = [
                    {
                        "title": a.title,
                        "author": a.author,
                        "page_number": a.page_start,
                    }
                    for a in real_mag.articles
                ]
                _reconcile_with_index(session, real_mag, real_articles_data)
                continue

            if virtual_mag:
                # Virtual record already exists — skip
                continue

            # Create virtual Magazine record
            mag = Magazine(
                title=publication,
                publication=publication,
                season=season,
                year=year,
                pdf_path=None,
            )
            session.add(mag)
            session.flush()

            for art in articles:
                article = Article(
                    magazine_id=mag.id,
                    title=art["title"],
                    author=art.get("author"),
                    page_start=None,
                    keywords=_auto_keywords(art["title"]),
                    extraction_method="index",
                )
                session.add(article)
                total_new_articles += 1

        session.commit()
        _progress(f"Done. {total_new_articles} new articles imported.")
        return total_new_articles

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    import sys
    from database.db import init_db

    if len(sys.argv) < 2:
        print("Usage: python -m core.scanner /path/to/magazine/folder")
        sys.exit(1)

    init_db()
    folder = sys.argv[1]
    new_pdfs = scan_directory(folder)
    print(f"New PDFs found: {len(new_pdfs)}")
    for p in new_pdfs:
        print(f"  {p}")

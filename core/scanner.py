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

import config
from database.db import get_session
from database.models import Magazine, Article
from core.toc_parser import parse_toc, has_text_layer
from core.ocr_engine import render_page_to_image
from core.ai_extractor import extract_toc_with_ai


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
    Attempt to extract title, volume, issue number, season, and year from the
    file name. Returns a dict with any fields that could be identified.

    Example filenames this handles:
        WildfowlCarving_Vol5_No2_Spring2003.pdf
        WoodCarving_Issue12_2005.pdf
    """
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    meta: dict = {"title": name, "volume": None, "issue_number": None,
                  "season": None, "year": None}

    if m := re.search(r"[Vv]ol(?:ume)?[\s_-]*(\d+)", name):
        meta["volume"] = int(m.group(1))

    if m := re.search(r"[Nn]o\.?[\s_-]*(\d+)|[Ii]ssue[\s_-]*(\d+)", name):
        meta["issue_number"] = int(m.group(1) or m.group(2))

    for season in ("Spring", "Summer", "Fall", "Autumn", "Winter"):
        if season.lower() in name.lower():
            meta["season"] = season
            break

    if m := re.search(r"(19|20)\d{2}", name):
        meta["year"] = int(m.group(0))

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
    _progress("Guessed metadata from filename.")

    # --- TOC extraction ---
    _progress("Detecting text layer…")
    articles_data, confidence = parse_toc(pdf_path, dpi=ocr_dpi,
                                          confidence_threshold=confidence_threshold)
    extraction_method = "pdf_text" if has_text_layer(pdf_path) else "ocr"

    # --- AI fallback ---
    if (confidence < confidence_threshold or len(articles_data) < 3) and api_key:
        use_ai = True
        if ask_before_ai and ai_confirm_callback:
            use_ai = ai_confirm_callback(os.path.basename(pdf_path))

        if use_ai:
            _progress("Running AI extraction (Claude Haiku)…")
            from core.toc_parser import find_toc_pages
            toc_pages = find_toc_pages(pdf_path, has_text_layer(pdf_path), ocr_dpi)
            if toc_pages:
                page_image = render_page_to_image(pdf_path, toc_pages[0], ocr_dpi)
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
            pdf_path=pdf_path,
            page_count=page_count,
            cover_image=cover_bytes,
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
        _progress(f"Imported {len(articles_data)} articles.")
        return magazine

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

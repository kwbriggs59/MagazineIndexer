"""
Table of Contents parser.

Detects whether a PDF has a text layer, finds the TOC page(s) among the
first 10 pages, and extracts article records using regex patterns.

Public API:
    parse_toc(pdf_path, dpi, confidence_threshold) -> (articles, confidence)

Run standalone for testing:
    python -m core.toc_parser /path/to/magazine.pdf
"""

from __future__ import annotations

import re
import fitz  # PyMuPDF
from typing import Optional

import config
from core.ocr_engine import ocr_page

# Minimum characters returned by get_text() to consider a page "searchable"
TEXT_LAYER_MIN_CHARS = 50

# Number of pages to scan when searching for the TOC
TOC_SCAN_PAGES = 10

# Patterns that identify a TOC page by keyword (matched against each line, not full text)
# Using line-level matching avoids "Contents copyright" in mastheads triggering a hit.
TOC_KEYWORD_RE = re.compile(
    r"^\s*(table\s+of\s+contents|contents|in this issue)\s*$", re.IGNORECASE
)

# Regex patterns to extract (title, author?, page_number) from TOC lines
TOC_PATTERNS = [
    # "Article Title .................. 42"  (clean dot leaders)
    re.compile(r"^(.+?)\s*[.\-]{3,}\s*(\d+)\s*$"),
    # "Article Title   Author Name   42"
    re.compile(r"^(.+?)\s{2,}([A-Z][a-z].+?)\s{2,}(\d+)$"),
    # "42   Article Title"
    re.compile(r"^(\d+)\s{2,}(.+?)(?:\s{2,}(.+?))?$"),
    # OCR-garbled dot leaders: title + noise/garbage + page number at end
    # e.g. "WINTER LAKESHORE  ...scccssesesssseessesseeeelQ 10" or "TITLE garbage 42"
    re.compile(r"^([A-Z][A-Z\s,\'\-\&]+?)\s+\S*\s*(\d{1,3})\s*$"),
    # Title with garbled leader where number is run onto noise: "TITLE ...garbage42"
    re.compile(r"^([A-Z][A-Z\s,\'\-\&]+?)\s+\S+(\d{1,3})\s*$"),
]

# Minimum number of lines ending in a page-like number to identify a TOC page.
# 15-char minimum before the number avoids short noise lines (captions, footers).
_TOC_LINE_MIN_HITS = 3
# TOC entries start with uppercase and end with a page number (1-3 digits).
# The digit may be glued to garbled dot-leader noise (no preceding space).
# Starting with [A-Z] filters out masthead lines, phone numbers, prices, etc.
_TOC_LINE_RE = re.compile(r"^[A-Z].{14,}\D\d{1,3}\s*$")

# Department headings to exclude from article results
EXCLUDED_HEADINGS = {
    "letters", "editor's note", "from the editor", "advertisers index",
    "advertisers", "index", "masthead", "credits", "subscription",
}


def has_text_layer(pdf_path: str) -> bool:
    """Return True if any of the first 3 pages contain >50 characters of extractable text."""
    doc = fitz.open(pdf_path)
    try:
        for i in range(min(3, doc.page_count)):
            if len(doc[i].get_text()) > TEXT_LAYER_MIN_CHARS:
                return True
    finally:
        doc.close()
    return False


def _is_toc_page(text: str) -> bool:
    """
    Return True if this page looks like a table of contents.
    Two strategies:
      1. Keyword match on individual lines only (avoids "Contents copyright" in mastheads).
      2. Heuristic: >= _TOC_LINE_MIN_HITS lines that end with a 1-3 digit number
         preceded by enough content — characteristic of dot-leader TOC entries
         even when OCR garbles the dots.
    """
    for line in text.splitlines():
        if TOC_KEYWORD_RE.match(line):
            return True
    hits = sum(1 for line in text.splitlines() if _TOC_LINE_RE.search(line))
    return hits >= _TOC_LINE_MIN_HITS


def find_toc_pages(pdf_path: str, has_text: bool, dpi: int = None) -> list[int]:
    """
    Scan the first TOC_SCAN_PAGES pages and return 0-indexed page numbers
    that appear to contain a table of contents.
    """
    if dpi is None:
        dpi = config.DEFAULT_OCR_DPI

    doc = fitz.open(pdf_path)
    toc_pages = []

    try:
        limit = min(TOC_SCAN_PAGES, doc.page_count)
        for i in range(limit):
            if has_text:
                text = doc[i].get_text()
            else:
                text, _ = ocr_page(pdf_path, i, dpi)

            if _is_toc_page(text):
                toc_pages.append(i)
    finally:
        doc.close()

    return toc_pages


def _parse_line(line: str) -> Optional[dict]:
    """
    Apply TOC_PATTERNS to a single line.
    Returns a dict with keys title, author, page_number, or None if no match.
    """
    line = line.strip()
    if not line:
        return None

    if line.lower() in EXCLUDED_HEADINGS:
        return None

    # Pattern A: title + clean dot leaders + page
    m = TOC_PATTERNS[0].match(line)
    if m:
        return {"title": m.group(1).strip(), "author": None, "page_number": int(m.group(2))}

    # Pattern B: title + author + page (two-space separated columns)
    m = TOC_PATTERNS[1].match(line)
    if m:
        return {"title": m.group(1).strip(), "author": m.group(2).strip(), "page_number": int(m.group(3))}

    # Pattern C: page + title (+ optional author)
    m = TOC_PATTERNS[2].match(line)
    if m:
        return {
            "title": m.group(2).strip(),
            "author": m.group(3).strip() if m.group(3) else None,
            "page_number": int(m.group(1)),
        }

    # Pattern D: ALL-CAPS title + OCR-garbled dots + page number (space before number)
    m = TOC_PATTERNS[3].match(line)
    if m:
        title = m.group(1).strip()
        if len(title) > 4:  # skip very short noise matches
            return {"title": title.title(), "author": None, "page_number": int(m.group(2))}

    # Pattern E: ALL-CAPS title + garbled noise run-together with page number
    m = TOC_PATTERNS[4].match(line)
    if m:
        title = m.group(1).strip()
        if len(title) > 4:
            return {"title": title.title(), "author": None, "page_number": int(m.group(2))}

    return None


def extract_articles_from_text(text: str) -> list[dict]:
    """Parse raw TOC text into a list of article dicts."""
    articles = []
    for line in text.splitlines():
        result = _parse_line(line)
        if result:
            articles.append(result)
    return articles


def parse_toc(
    pdf_path: str,
    dpi: int = None,
    confidence_threshold: int = None,
) -> tuple[list[dict], float]:
    """
    Full TOC parsing pipeline for one PDF.

    Returns:
        (articles, mean_confidence)
        articles is a list of dicts: {title, author, page_number}
        mean_confidence is 0–100 (100 if text layer was used; OCR score otherwise)
    """
    if dpi is None:
        dpi = config.DEFAULT_OCR_DPI
    if confidence_threshold is None:
        confidence_threshold = config.DEFAULT_OCR_CONFIDENCE_THRESHOLD

    text_layer = has_text_layer(pdf_path)
    toc_pages = find_toc_pages(pdf_path, text_layer, dpi)

    all_articles: list[dict] = []
    mean_confidence = 100.0  # Perfect confidence for text-layer PDFs

    doc = fitz.open(pdf_path)
    try:
        for page_num in toc_pages:
            if text_layer:
                text = doc[page_num].get_text()
                confidence = 100.0
            else:
                text, confidence = ocr_page(pdf_path, page_num, dpi)
                mean_confidence = min(mean_confidence, confidence)

            articles = extract_articles_from_text(text)
            all_articles.extend(articles)
    finally:
        doc.close()

    return all_articles, mean_confidence


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m core.toc_parser /path/to/magazine.pdf")
        sys.exit(1)

    pdf = sys.argv[1]
    articles, confidence = parse_toc(pdf)
    print(f"Confidence: {confidence:.1f}%")
    print(f"Articles found: {len(articles)}")
    print(json.dumps(articles, indent=2))

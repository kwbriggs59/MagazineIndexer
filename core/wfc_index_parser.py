"""
Parser for the Wildfowl Carving master index PDF.

The PDF uses two main article formats across different eras:
  Newer (2000s+): "Title (desc.) Author Name. [Includes a pattern.]"
  Older (1980s+): "Title by Author Name."
  No-author:      "Title"  -- skipped (no author found)

Long entries wrap across two lines. Typical wraps:
  "Title (description that overflows) Author"   <- ends without period/author-period
  "Name."                                        <- continuation: just the last name + period

  "Title by Author First"                        <- ends without period
  "Last"                                         <- continuation: just last name

Public API:
    parse_wfc_index(pdf_path) -> list[dict]

Each dict: {season, year, title, author}
"""

from __future__ import annotations

import re
import fitz  # PyMuPDF


# Issue header: "Winter 2023" or "Fall 1985 (SOLD OUT!)"
_ISSUE_HEADER_RE = re.compile(
    r'^(Winter|Spring|Summer|Fall|Autumn)(?:[/\\-](?:Winter|Spring|Summer|Fall|Autumn))?\s+(\d{4})',
    re.IGNORECASE,
)

# A line that "looks complete" — ends with one of:
#   - "Name."          (Author Last.)
#   - "Name. Includes ..."
#   - "pattern."
#   - ")"              (cover note, or article with no author)
#   - year in header
_COMPLETE_LINE_RE = re.compile(
    r'[.!?)]'          # ends with sentence-ending or closing paren
    r'\s*'
    r'(?:Includes\b.*)?'
    r'\s*$',
    re.IGNORECASE,
)

# "by Author Name" near the end (handles "byTom" no-space edge case)
_BY_AUTHOR_RE = re.compile(
    r'\bby\s*([A-Z][a-z]+(?:\s+[A-Z][a-z.,]+){0,3})\s*[.,]?\s*(?:Includes\b.*)?$'
)

# Author follows closing paren: ") First Last." or ") First Last"
# Requires at least 2 name words (first + last) to avoid false positives on single words
# Uses lazy quantifier to prefer shortest match (2 words preferred over 3-4)
_CLOSE_PAREN_AUTHOR_RE = re.compile(
    r'\)\s+([A-Z][a-z]+\s+[A-Z][a-z.,]+(?:\s+[A-Z][a-z.,]+)?)[.,]?\s*(?:Includes\b.*)?$'
)

# Author after PERIOD (not just any space): ". First Last."
# Requires period/colon immediately before the author, to avoid false positives on
# lines like "Painting Notes: Olive Warbler Jerry Poindexter" (no period before author)
_DOT_SPACE_AUTHOR_RE = re.compile(
    r'[.]\s+([A-Z][a-z]+\s+[A-Z][a-z.,]+(?:\s+[A-Z][a-z.,]+)?)[.,]?\s*(?:Includes\b.*)?$'
)

# "Includes ..." suffix
_INCLUDES_RE = re.compile(r'[.,]?\s*Includes\b.*$', re.IGNORECASE)

# Trailing parenthetical description in title
_PAREN_SUFFIX_RE = re.compile(r'\s*\([^)]+\)\s*$')


def _extract_raw_lines(pdf_path: str) -> list[str]:
    """Return stripped lines from all pages of the PDF."""
    doc = fitz.open(pdf_path)
    lines: list[str] = []
    try:
        for page in doc:
            for line in page.get_text("text").splitlines():
                lines.append(line.strip())
    finally:
        doc.close()
    return lines


def _looks_complete(line: str) -> bool:
    """
    Return True if line already looks like a complete article entry
    (has both a title and an identifiable author).
    """
    line = line.rstrip()
    # Has "by Author" pattern
    if _BY_AUTHOR_RE.search(line):
        return True
    # Has ") Author Name." pattern (author follows closing paren of description)
    if _CLOSE_PAREN_AUTHOR_RE.search(line):
        return True
    # Has ". Author Name." pattern
    if _DOT_SPACE_AUTHOR_RE.search(line):
        return True
    # Has trailing "First Last" (two Title Case words) after paren/period/space
    if re.search(r'[). ]\s*[A-Z][a-z]+\s+[A-Z][a-z.]+\s*$', line):
        return True
    return False


def _is_continuation(prev: str, curr: str) -> bool:
    """
    Return True if curr is a continuation of prev (i.e., they should be joined).

    A line is a continuation of the previous if:
    - The previous line does NOT end with a complete sentence/author marker, AND
    - The current line is a short fragment (likely just a surname, or "Name." or "pattern.")

    We consider prev complete if it ends with:
      - ". Includes..." or ", Includes..."
      - "Name." where Name is Title Case (author period)
      - ")" — closing paren (description-only articles)
    """
    if not prev or not curr:
        return False
    if _ISSUE_HEADER_RE.match(curr) or _ISSUE_HEADER_RE.match(prev):
        return False
    if curr.startswith("(The ") or prev.startswith("(The "):
        return False

    # If curr starts lowercase it's always a continuation
    if curr[0].islower():
        return True

    # If the previous line already looks like a complete article → no join
    if _looks_complete(prev):
        return False

    # If prev ends with something "complete" → no join
    prev_r = prev.rstrip()
    # Check: ends with "period" after Includes → complete
    if re.search(r'Includes\b.*[.!?]$', prev_r, re.IGNORECASE):
        return False
    # Ends with "." after a Title-Case word → likely complete
    if re.search(r'[A-Z][a-z]+\.$', prev_r):
        return False
    # Ends with ")" → check if curr is a dangling author name (continuation case)
    if prev_r.endswith(')'):
        # If curr looks like just a person name (1-4 words, all Title Case)
        # e.g. "Jerry Simchuk" or "Floyd Scholz" or "Ben Heinemann. Includes a pattern."
        if re.match(r'^[A-Z][a-z.]+(?:\s+[A-Z][a-z.]+){0,3}[.,]?\s*(?:Includes\b.*)?$', curr) and len(curr) <= 60:
            return True  # dangling author — join
        return False  # otherwise closing paren ends the entry
    # Ends with "." → complete
    if prev_r.endswith('.'):
        return False

    # Previous line is incomplete → join
    return True


def _join_wrapped_lines(raw_lines: list[str]) -> list[str]:
    """Join wrapped continuation lines into single logical article lines."""
    result: list[str] = []
    buffer = ""

    def flush():
        nonlocal buffer
        s = buffer.strip()
        if s:
            result.append(s)
        buffer = ""

    for raw in raw_lines:
        stripped = raw

        if not stripped:
            flush()
            continue

        if _ISSUE_HEADER_RE.match(stripped):
            flush()
            result.append(stripped)
            continue

        if stripped.startswith("(The "):
            flush()
            result.append(stripped)
            continue

        if buffer:
            if _is_continuation(buffer, stripped):
                buffer = buffer.rstrip() + " " + stripped
            else:
                flush()
                buffer = stripped
        else:
            buffer = stripped

    flush()
    return result


def _strip_includes(line: str) -> str:
    return _INCLUDES_RE.sub('', line).strip()


def _parse_article_line(line: str) -> dict | None:
    """
    Extract title and author from one logical article line.
    Returns None if no author can be identified.
    """
    line = _strip_includes(line)

    # Strategy A: "by Author Name" pattern
    m = _BY_AUTHOR_RE.search(line)
    if m:
        author = m.group(1).strip().rstrip('.,')
        before = line[:m.start()].strip().rstrip('.,-(').strip()
        title = _PAREN_SUFFIX_RE.sub('', before).strip().rstrip('.,').strip()
        if title and len(title) >= 4 and len(author) >= 4:
            return {"title": title, "author": author}

    # Strategy B: ") Author Name." — author follows closing paren of description
    m = _CLOSE_PAREN_AUTHOR_RE.search(line)
    if m:
        author = m.group(1).strip().rstrip('.,')
        # Title is everything up to and including the )
        before = line[:m.start() + 1].strip()
        title = _PAREN_SUFFIX_RE.sub('', before).strip().rstrip('.,').strip()
        if title and len(title) >= 4 and len(author) >= 4:
            return {"title": title, "author": author}

    # Strategy C: ". Author Name." — author follows a period separator
    m = _DOT_SPACE_AUTHOR_RE.search(line)
    if m:
        author = m.group(1).strip().rstrip('.,')
        before = line[:m.start() + 1].strip()  # include the period
        title = _PAREN_SUFFIX_RE.sub('', before).strip().rstrip('.,').strip()
        if title and len(title) >= 4 and len(author) >= 4:
            return {"title": title, "author": author}

    # Strategy D: last two Title-Case words at end of line (fallback for lines like
    # "Painting Notes: Olive Warbler Jerry Poindexter" with no structural separator).
    # Only applies if line has at least 5 words total (to avoid false positives on short lines).
    words = line.split()
    if len(words) >= 5:
        # Try last 2 words as author
        last2 = words[-2] + " " + words[-1]
        last2_clean = last2.rstrip('.,')
        if re.match(r'^[A-Z][a-z.]+\s+[A-Z][a-z.]+$', last2_clean) and len(last2_clean) >= 7:
            before_words = words[:-2]
            before = " ".join(before_words).strip().rstrip('.,').strip()
            title = _PAREN_SUFFIX_RE.sub('', before).strip().rstrip('.,').strip()
            if title and len(title) >= 4:
                return {"title": title, "author": last2_clean}

    return None


def parse_wfc_index(pdf_path: str) -> list[dict]:
    """
    Parse a Wildfowl Carving master index PDF.
    Returns list of {season, year, title, author} dicts.
    """
    raw_lines = _extract_raw_lines(pdf_path)
    logical_lines = _join_wrapped_lines(raw_lines)

    results: list[dict] = []
    current_season: str | None = None
    current_year: int | None = None
    seen: set[tuple] = set()

    for line in logical_lines:
        if not line or len(line) < 6:
            continue

        m = _ISSUE_HEADER_RE.match(line)
        if m:
            season_raw = m.group(1).capitalize()
            if season_raw == "Autumn":
                season_raw = "Fall"
            current_season = season_raw
            current_year = int(m.group(2))
            continue

        if line.startswith("(The "):
            continue
        if 'control' in line.lower() and 'search' in line.lower():
            continue

        if current_season is None or current_year is None:
            continue

        article = _parse_article_line(line)
        if article:
            key = (current_season, current_year, article["title"].lower())
            if key not in seen:
                seen.add(key)
                results.append({
                    "season": current_season,
                    "year": current_year,
                    "title": article["title"],
                    "author": article["author"],
                })

    return results


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print("Usage: python -m core.wfc_index_parser path/to/index.pdf")
        sys.exit(1)

    results = parse_wfc_index(sys.argv[1])
    print(f"Total articles: {len(results)}")
    for r in results[:10]:
        print(f"  {r['season']} {r['year']}  author={r['author']!r:<25} {r['title']}")

    from collections import Counter
    issues = Counter((r["season"], r["year"]) for r in results)
    print(f"Issues found: {len(issues)}")

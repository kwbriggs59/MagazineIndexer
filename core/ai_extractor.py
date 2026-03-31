"""
AI Fallback Extractor — uses Claude Haiku to extract TOC data from a page image.

Called only when OCR confidence falls below the configured threshold.
All API calls are logged to ai_usage.log with timestamp, tokens, and cost estimate.

Public API:
    extract_toc_with_ai(page_image, api_key) -> list[dict]
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from io import BytesIO

import anthropic
from PIL import Image

import config

logger = logging.getLogger(__name__)

# Cost estimate per token (haiku pricing as of 2024 — update if pricing changes)
_COST_PER_INPUT_TOKEN = 0.00000025   # $0.25 per 1M input tokens
_COST_PER_OUTPUT_TOKEN = 0.00000125  # $1.25 per 1M output tokens

TOC_PROMPT = """You are extracting a table of contents from a scanned magazine page.
Return ONLY a JSON array with no markdown, no explanation, no code fences.
Each object must have exactly these keys:
  "title": article title as a string
  "author": author name as a string, or null if not listed
  "page": page number as an integer, or null if not listed

Include every article. Exclude department headings such as
"Letters", "Editor's Note", "From the Editor", or "Advertisers Index"."""


def _log_usage(input_tokens: int, output_tokens: int) -> None:
    """Append a usage record to ai_usage.log."""
    cost = (input_tokens * _COST_PER_INPUT_TOKEN) + (output_tokens * _COST_PER_OUTPUT_TOKEN)
    entry = (
        f"{datetime.now().isoformat()} | "
        f"model={config.AI_MODEL} | "
        f"input_tokens={input_tokens} | "
        f"output_tokens={output_tokens} | "
        f"est_cost=${cost:.6f}\n"
    )
    try:
        with open(config.AI_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as e:
        logger.warning("Could not write to ai_usage.log: %s", e)


def extract_toc_with_ai(page_image: Image.Image, api_key: str) -> list[dict]:
    """
    Encode a PIL Image of a TOC page as base64 and send it to Claude Haiku.

    Args:
        page_image: PIL Image of the TOC page (any mode; converted to PNG internally).
        api_key:    Anthropic API key.

    Returns:
        List of dicts with keys: title (str), author (str|None), page (int|None).

    Raises:
        ValueError: If the API response cannot be parsed as a JSON array.
        anthropic.APIError: On API communication failure.
    """
    buf = BytesIO()
    page_image.save(buf, format="PNG")
    b64 = base64.standard_b64encode(buf.getvalue()).decode()

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=config.AI_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {"type": "text", "text": TOC_PROMPT},
            ],
        }],
    )

    _log_usage(response.usage.input_tokens, response.usage.output_tokens)

    raw = response.content[0].text.strip()
    try:
        articles = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI response was not valid JSON: {e}\nResponse: {raw}") from e

    if not isinstance(articles, list):
        raise ValueError(f"AI response was not a JSON array. Got: {type(articles)}")

    return articles

"""
OCR Engine — renders PDF pages to images and runs Tesseract OCR.

Public API:
    ocr_page(pdf_path, page_num, dpi) -> (text, mean_confidence)
    render_page_to_image(pdf_path, page_num, dpi) -> PIL.Image
    ocr_confidence(tesseract_data) -> float
"""

from __future__ import annotations

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from io import BytesIO

import config


def render_page_to_image(pdf_path: str, page_num: int, dpi: int = None) -> Image.Image:
    """
    Render a single PDF page to a PIL Image using PyMuPDF.

    Args:
        pdf_path: Absolute path to the PDF file.
        page_num: 0-indexed page number.
        dpi:      Render resolution. Defaults to config.DEFAULT_OCR_DPI.

    Returns:
        A PIL Image of the rendered page.
    """
    if dpi is None:
        dpi = config.DEFAULT_OCR_DPI

    doc = fitz.open(pdf_path)
    page = doc[page_num]
    zoom = dpi / 72.0  # PyMuPDF default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    doc.close()

    img = Image.open(BytesIO(pix.tobytes("png")))
    return img


def ocr_confidence(tesseract_data: dict) -> float:
    """
    Calculate mean word confidence from pytesseract image_to_data output.

    Args:
        tesseract_data: Dict returned by pytesseract.image_to_data(..., output_type=DICT).

    Returns:
        Mean confidence as a float 0–100. Returns 0.0 if no words detected.
    """
    confidences = [int(c) for c in tesseract_data["conf"] if int(c) > 0]
    return sum(confidences) / len(confidences) if confidences else 0.0


def ocr_page(pdf_path: str, page_num: int, dpi: int = None) -> tuple[str, float]:
    """
    Render a PDF page and run Tesseract OCR on it.

    Args:
        pdf_path: Absolute path to the PDF file.
        page_num: 0-indexed page number.
        dpi:      Render resolution. Defaults to config.DEFAULT_OCR_DPI.

    Returns:
        (extracted_text, mean_confidence) where confidence is 0–100.
    """
    if dpi is None:
        dpi = config.DEFAULT_OCR_DPI

    image = render_page_to_image(pdf_path, page_num, dpi)
    lang = config.DEFAULT_OCR_LANGUAGE
    psm_config = "--psm 6"  # Assume uniform block of text

    data = pytesseract.image_to_data(
        image,
        lang=lang,
        output_type=pytesseract.Output.DICT,
        config=psm_config,
    )
    text = pytesseract.image_to_string(image, lang=lang, config=psm_config)
    confidence = ocr_confidence(data)

    return text, confidence

"""
PDF Reader Panel — right side of the main window.

Uses PyMuPDF for rendering and QScrollArea + QLabel for display.
Caches the last PAGE_CACHE_SIZE rendered pages to avoid re-rendering
on simple back/forward navigation.

Public slots:
    open_article(article_id: int) — opens the correct PDF and jumps to page_start
    open_pdf(pdf_path: str, page_num: int) — open a specific PDF at a specific page
"""

from __future__ import annotations

from collections import OrderedDict

import fitz  # PyMuPDF
from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QLineEdit, QSizePolicy,
)

import config
from database.db import get_session
from database.models import Article


ZOOM_STEPS = [0.25, 0.50, 0.75, 1.0, 1.25, 1.50, 2.0]
DEFAULT_ZOOM_INDEX = 3  # 100%


class ReaderPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pdf_path: str | None = None
        self._doc: fitz.Document | None = None
        self._current_page = 0
        self._zoom_index = DEFAULT_ZOOM_INDEX
        self._page_cache: OrderedDict[tuple[str, int, float], QPixmap] = OrderedDict()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Navigation toolbar ---
        nav = QHBoxLayout()
        self._prev_btn = QPushButton("◄ Prev")
        self._prev_btn.clicked.connect(self._go_prev)
        nav.addWidget(self._prev_btn)

        self._page_input = QLineEdit()
        self._page_input.setFixedWidth(50)
        self._page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_input.returnPressed.connect(self._on_page_input)
        nav.addWidget(self._page_input)

        self._page_total = QLabel("/ 0")
        nav.addWidget(self._page_total)

        self._next_btn = QPushButton("Next ►")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)

        nav.addStretch()

        # Zoom controls
        zoom_out = QPushButton("−")
        zoom_out.setFixedWidth(30)
        zoom_out.clicked.connect(self._zoom_out)
        nav.addWidget(zoom_out)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(50)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self._zoom_label)

        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(30)
        zoom_in.clicked.connect(self._zoom_in)
        nav.addWidget(zoom_in)

        fit_page = QPushButton("Fit Page")
        fit_page.clicked.connect(self._fit_page)
        nav.addWidget(fit_page)

        fit_width = QPushButton("Fit Width")
        fit_width.clicked.connect(self._fit_width)
        nav.addWidget(fit_width)

        layout.addLayout(nav)

        # --- Scroll area for the page image ---
        self._scroll = QScrollArea()
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidgetResizable(False)

        self._page_label = QLabel("No PDF open")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._scroll.setWidget(self._page_label)

        layout.addWidget(self._scroll)

    # --- Public API ---

    def open_article(self, article_id: int):
        """Open the PDF associated with an article and jump to its start page."""
        session = get_session()
        try:
            article = session.get(Article, article_id)
            if article is None:
                return
            pdf_path = article.magazine.pdf_path
            page_start = (article.page_start or 1) - 1  # convert 1-indexed to 0-indexed
        finally:
            session.close()

        self.open_pdf(pdf_path, max(0, page_start))

    def open_pdf(self, pdf_path: str, page_num: int = 0):
        """Open a PDF file and display the specified page (0-indexed)."""
        if self._doc and self._pdf_path != pdf_path:
            self._doc.close()
            self._doc = None
            self._page_cache.clear()

        if self._doc is None:
            self._doc = fitz.open(pdf_path)
            self._pdf_path = pdf_path

        total = self._doc.page_count
        self._current_page = max(0, min(page_num, total - 1))
        self._page_total.setText(f"/ {total}")
        self._render_current()

    # --- Navigation ---

    def _go_prev(self):
        if self._doc and self._current_page > 0:
            self._current_page -= 1
            self._render_current()

    def _go_next(self):
        if self._doc and self._current_page < self._doc.page_count - 1:
            self._current_page += 1
            self._render_current()

    def _on_page_input(self):
        if not self._doc:
            return
        try:
            page = int(self._page_input.text()) - 1  # UI is 1-indexed
            self._current_page = max(0, min(page, self._doc.page_count - 1))
            self._render_current()
        except ValueError:
            pass

    # --- Zoom ---

    def _zoom_in(self):
        if self._zoom_index < len(ZOOM_STEPS) - 1:
            self._zoom_index += 1
            self._render_current()

    def _zoom_out(self):
        if self._zoom_index > 0:
            self._zoom_index -= 1
            self._render_current()

    def _fit_page(self):
        """Set zoom so the full page height fits in the scroll area."""
        if not self._doc:
            return
        page = self._doc[self._current_page]
        avail_h = self._scroll.height()
        zoom = avail_h / page.rect.height
        self._set_custom_zoom(zoom)

    def _fit_width(self):
        """Set zoom so the page width fills the scroll area."""
        if not self._doc:
            return
        page = self._doc[self._current_page]
        avail_w = self._scroll.width() - 20  # scrollbar margin
        zoom = avail_w / page.rect.width
        self._set_custom_zoom(zoom)

    def _set_custom_zoom(self, zoom: float):
        """Find the closest zoom step and re-render."""
        closest = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - zoom))
        self._zoom_index = closest
        self._render_current()

    # --- Rendering ---

    def _render_current(self):
        if not self._doc:
            return

        zoom = ZOOM_STEPS[self._zoom_index]
        cache_key = (self._pdf_path, self._current_page, zoom)

        if cache_key not in self._page_cache:
            page = self._doc[self._current_page]
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            qba = QByteArray(pix.tobytes("png"))
            pixmap = QPixmap()
            pixmap.loadFromData(qba, "PNG")

            self._page_cache[cache_key] = pixmap
            if len(self._page_cache) > config.PAGE_CACHE_SIZE:
                self._page_cache.popitem(last=False)
        else:
            # Move to end (most recently used)
            self._page_cache.move_to_end(cache_key)
            pixmap = self._page_cache[cache_key]

        self._page_label.setPixmap(pixmap)
        self._page_label.resize(pixmap.size())
        self._page_input.setText(str(self._current_page + 1))
        self._zoom_label.setText(f"{int(zoom * 100)}%")

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self._zoom_in()
            else:
                self._zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

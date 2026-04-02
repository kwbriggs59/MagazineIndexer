"""
Magazine Grid — home screen showing cover thumbnails for all owned issues.

Each card displays the cover image, publication name, and issue date.
Right-clicking a card shows Re-import and Delete options.

Signals:
    magazine_selected(mag_id: int)   — user left-clicked a card
    reimport_requested(pdf_path: str) — user chose Re-import from context menu
    delete_requested(mag_id: int)    — user confirmed Delete from context menu
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal, Qt, QByteArray
from PyQt6.QtGui import QPixmap, QAction
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QGridLayout,
    QLabel, QComboBox, QFrame, QMenu, QMessageBox, QSizePolicy,
)

from database.db import get_session
from database.models import Magazine, Article


CARD_W = 165
CARD_H = 250
THUMB_W = 150
THUMB_H = 195
COLS = 5


class _MagazineCard(QFrame):
    """Clickable card showing a magazine cover thumbnail + metadata."""

    clicked = pyqtSignal(int)           # mag_id
    reimport_requested = pyqtSignal(str)  # pdf_path
    delete_requested = pyqtSignal(int)  # mag_id

    def __init__(
        self,
        mag_id: int,
        publication: str,
        subtitle: str,
        cover_bytes: bytes | None,
        pdf_path: str | None,
        parent=None,
    ):
        super().__init__(parent)
        self._mag_id = mag_id
        self._pdf_path = pdf_path

        self.setFixedSize(CARD_W, CARD_H)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        # Cover thumbnail
        thumb = QLabel()
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedSize(THUMB_W, THUMB_H)
        thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if cover_bytes:
            pixmap = QPixmap()
            pixmap.loadFromData(QByteArray(cover_bytes), "PNG")
            thumb.setPixmap(
                pixmap.scaled(THUMB_W, THUMB_H,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            )
        else:
            thumb.setText("No Cover")
            thumb.setStyleSheet("background: #cccccc; color: #666666;")
        layout.addWidget(thumb)

        # Publication name
        pub_lbl = QLabel(publication)
        pub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pub_lbl.setWordWrap(True)
        pub_lbl.setStyleSheet("font-weight: bold; font-size: 10px;")
        pub_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(pub_lbl)

        # Season / year
        sub_lbl = QLabel(subtitle)
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setStyleSheet("font-size: 9px;")
        sub_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(sub_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._mag_id)
        super().mousePressEvent(event)

    def _show_context_menu(self, pos):
        menu = QMenu(self)

        reimport_action = QAction("Re-import Issue…", self)
        reimport_action.triggered.connect(self._on_reimport)
        menu.addAction(reimport_action)

        menu.addSeparator()

        delete_action = QAction("Delete Issue…", self)
        delete_action.triggered.connect(self._on_delete)
        menu.addAction(delete_action)

        menu.exec(self.mapToGlobal(pos))

    def _on_reimport(self):
        if not self._pdf_path:
            QMessageBox.information(
                self,
                "Catalog-Only Entry",
                "This is a catalog-only entry with no PDF. Nothing to re-import.",
            )
            return
        self.reimport_requested.emit(self._pdf_path)

    def _on_delete(self):
        self.delete_requested.emit(self._mag_id)


class MagazineGrid(QWidget):
    magazine_selected = pyqtSignal(int)    # mag_id
    reimport_requested = pyqtSignal(str)   # pdf_path
    delete_requested = pyqtSignal(int)     # mag_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._magazines: list[dict] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Magazine:"))
        self._pub_filter = QComboBox()
        self._pub_filter.addItem("All")
        self._pub_filter.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pub_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._pub_filter)

        filter_row.addSpacing(16)
        filter_row.addWidget(QLabel("Owned:"))
        self._owned_filter = QComboBox()
        self._owned_filter.addItems(["All", "Owned", "Not Owned"])
        self._owned_filter.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._owned_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._owned_filter)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Scroll area holding the card grid
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._scroll.setWidget(self._grid_widget)
        layout.addWidget(self._scroll)

    def refresh(self):
        """Reload all magazine data from the DB and rebuild the grid."""
        session = get_session()
        try:
            mags = (
                session.query(Magazine)
                .order_by(Magazine.publication, Magazine.year.desc(), Magazine.season)
                .all()
            )
            self._magazines = [
                {
                    "id": m.id,
                    "publication": m.publication or m.title or "",
                    "season": m.season or "",
                    "year": m.year,
                    "cover_image": m.cover_image,
                    "pdf_path": m.pdf_path,
                }
                for m in mags
            ]
            pubs = sorted({m["publication"] for m in self._magazines if m["publication"]})
        finally:
            session.close()

        # Repopulate publication filter, preserving current selection
        current_pub = self._pub_filter.currentText()
        self._pub_filter.blockSignals(True)
        self._pub_filter.clear()
        self._pub_filter.addItem("All")
        for p in pubs:
            self._pub_filter.addItem(p)
        idx = self._pub_filter.findText(current_pub)
        self._pub_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._pub_filter.blockSignals(False)

        self._apply_filter()

    def _apply_filter(self):
        pub = self._pub_filter.currentText()
        owned = self._owned_filter.currentText()
        filtered = [
            m for m in self._magazines
            if (pub == "All" or m["publication"] == pub)
            and (
                owned == "All"
                or (owned == "Owned" and m["pdf_path"] is not None)
                or (owned == "Not Owned" and m["pdf_path"] is None)
            )
        ]
        self._rebuild_grid(filtered)

    def _rebuild_grid(self, magazines: list[dict]):
        # Remove and destroy existing cards
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not magazines:
            lbl = QLabel("No magazines in library.\nClick 'Scan Now' to import PDFs.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888; font-size: 14px;")
            self._grid.addWidget(lbl, 0, 0, 1, COLS, Qt.AlignmentFlag.AlignCenter)
            return

        for idx, m in enumerate(magazines):
            subtitle = " ".join(p for p in [m["season"], str(m["year"]) if m["year"] else None] if p)
            card = _MagazineCard(
                mag_id=m["id"],
                publication=m["publication"],
                subtitle=subtitle,
                cover_bytes=m["cover_image"],
                pdf_path=m["pdf_path"],
            )
            card.clicked.connect(self.magazine_selected)
            card.reimport_requested.connect(self.reimport_requested)
            card.delete_requested.connect(self.delete_requested)
            row, col = divmod(idx, COLS)
            self._grid.addWidget(card, row, col)

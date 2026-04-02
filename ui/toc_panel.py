"""
TOC Panel — left panel shown when a magazine is open in the reader.

Displays the table of contents for a single magazine issue.
Clicking an article row navigates the PDF viewer to that page.
Right-clicking the header shows "Set Page Offset…".

Signals:
    article_selected(article_id: int) — user clicked an article
    back_requested()                  — user clicked the Back button
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal, Qt

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QSplitter,
)

from database.db import get_session
from database.models import Magazine, Article
from ui.article_detail import ArticleDetail


class TocPanel(QWidget):
    article_selected = pyqtSignal(int)  # article_id
    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mag_id: int | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Back button row
        top_row = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.setFixedWidth(80)
        back_btn.clicked.connect(self.back_requested)
        top_row.addWidget(back_btn)
        top_row.addStretch()
        layout.addLayout(top_row)

        # Issue label — right-click for "Set Page Offset"
        self._issue_label = QLabel()
        self._issue_label.setWordWrap(True)
        self._issue_label.setStyleSheet("font-weight: bold; padding: 2px 0;")
        self._issue_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._issue_label.customContextMenuRequested.connect(self._on_header_context_menu)
        layout.addWidget(self._issue_label)

        # Article list + detail panel in a vertical splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_item_clicked)
        splitter.addWidget(self._list)

        self._detail = ArticleDetail()
        splitter.addWidget(self._detail)

        splitter.setSizes([300, 250])
        layout.addWidget(splitter)

    def load_magazine(self, mag_id: int):
        """Populate the TOC for the given magazine."""
        self._mag_id = mag_id
        self._list.clear()

        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            if mag is None:
                self._issue_label.setText("(unknown issue)")
                return

            parts = [p for p in [mag.publication or mag.title, mag.season,
                                  str(mag.year) if mag.year else None] if p]
            self._issue_label.setText(" — ".join(parts))

            articles = sorted(
                mag.articles,
                key=lambda a: (a.page_start is None, a.page_start or 0),
            )
            for art in articles:
                page_str = str(art.page_start) if art.page_start is not None else "—"
                text = f"p.{page_str}  {art.title}"
                if art.author:
                    text += f"\n    {art.author}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, art.id)
                self._list.addItem(item)
        finally:
            session.close()

    def _on_item_clicked(self, item: QListWidgetItem):
        article_id = item.data(Qt.ItemDataRole.UserRole)
        if article_id is not None:
            self._detail.load_article(article_id)
            self.article_selected.emit(article_id)

    def _on_header_context_menu(self, pos):
        if self._mag_id is None:
            return
        menu = QMenu(self)
        action = QAction("Set Page Offset…", self)
        action.triggered.connect(self._set_page_offset)
        menu.addAction(action)
        menu.exec(self._issue_label.mapToGlobal(pos))

    def _set_page_offset(self):
        if self._mag_id is None:
            return

        session = get_session()
        try:
            mag = session.get(Magazine, self._mag_id)
            current = mag.page_offset or 0 if mag else 0
        finally:
            session.close()

        value, ok = QInputDialog.getInt(
            self,
            "Set Page Offset",
            "Number of unnumbered pages before page 1\n"
            "(auto-detected on import — adjust if articles open to the wrong page)",
            current, -50, 50,
        )
        if not ok:
            return

        session = get_session()
        try:
            mag = session.get(Magazine, self._mag_id)
            if mag:
                mag.page_offset = value
                session.commit()
        finally:
            session.close()

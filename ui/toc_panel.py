"""
TOC Panel — left panel shown when a magazine is open in the reader.

Displays the table of contents for a single magazine issue.
Clicking an article row navigates the PDF viewer to that page.

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
    QMessageBox,
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
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(28)
        add_btn.setToolTip("Add article")
        add_btn.clicked.connect(self._add_article)
        top_row.addWidget(add_btn)
        layout.addLayout(top_row)

        # Issue label
        self._issue_label = QLabel()
        self._issue_label.setWordWrap(True)
        self._issue_label.setStyleSheet("font-weight: bold; padding: 2px 0;")
        layout.addWidget(self._issue_label)

        # Page offset row
        offset_row = QHBoxLayout()
        self._offset_label = QLabel("Page offset: —")
        self._offset_label.setStyleSheet("font-size: 11px; color: #888;")
        offset_row.addWidget(self._offset_label)
        offset_row.addStretch()
        offset_btn = QPushButton("Edit")
        offset_btn.setFixedWidth(40)
        offset_btn.setToolTip("Set page offset for this issue")
        offset_btn.clicked.connect(self._set_page_offset)
        offset_row.addWidget(offset_btn)
        layout.addLayout(offset_row)

        # Article list + detail panel in a vertical splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_article_context_menu)
        splitter.addWidget(self._list)

        self._detail = ArticleDetail()
        self._detail.article_changed.connect(self._on_article_changed)
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
            self._offset_label.setText(f"Page offset: {mag.page_offset or 0}")

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

    def _on_article_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return
        article_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        delete_action = QAction("Delete Article", self)
        delete_action.triggered.connect(lambda: self._delete_article(article_id))
        menu.addAction(delete_action)
        menu.exec(self._list.mapToGlobal(pos))

    def _add_article(self):
        if self._mag_id is None:
            return
        session = get_session()
        try:
            article = Article(
                magazine_id=self._mag_id,
                title="New Article",
                extraction_method="manual",
            )
            session.add(article)
            session.commit()
            new_id = article.id
        finally:
            session.close()
        self.load_magazine(self._mag_id)
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == new_id:
                self._list.setCurrentItem(item)
                self._detail.load_article(new_id)
                break

    def _delete_article(self, article_id: int):
        reply = QMessageBox.question(
            self, "Delete Article",
            "Remove this article from the TOC?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        session = get_session()
        try:
            article = session.get(Article, article_id)
            if article:
                session.delete(article)
                session.commit()
        finally:
            session.close()
        self._detail.setEnabled(False)
        self.load_magazine(self._mag_id)

    def _on_article_changed(self):
        """Refresh the list text after an article is edited and saved."""
        if self._mag_id is not None:
            current_id = None
            current = self._list.currentItem()
            if current:
                current_id = current.data(Qt.ItemDataRole.UserRole)
            self.load_magazine(self._mag_id)
            if current_id is not None:
                for i in range(self._list.count()):
                    item = self._list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == current_id:
                        self._list.setCurrentItem(item)
                        break

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
        self._offset_label.setText(f"Page offset: {value}")

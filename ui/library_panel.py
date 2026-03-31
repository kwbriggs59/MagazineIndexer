"""
Library Panel — left side of the main window.

QTreeWidget with three hierarchy levels:
  Level 1: Magazine title  (e.g. "Wildfowl Carving")
  Level 2: Issue           (e.g. "Vol 5 No 2 — Spring 2003")
  Level 3: Article row     (read indicator · title · author · page · rating)

Signals:
  article_selected(article_id: int)  — emitted when user clicks an article row
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QMenu, QComboBox, QCheckBox, QHBoxLayout, QLabel,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QAction

from database.db import get_session
from database.models import Magazine, Article
from ui.article_detail import ArticleDetail


class LibraryPanel(QWidget):
    article_selected = pyqtSignal(int)  # article_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Filter / sort controls ---
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Sort:"))

        self._sort_combo = QComboBox()
        self._sort_combo.addItems([
            "Date (newest first)", "Title", "Author", "Rating", "Unread First"
        ])
        self._sort_combo.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self._sort_combo)

        self._unread_only = QCheckBox("Unread only")
        self._unread_only.stateChanged.connect(self.refresh)
        controls.addWidget(self._unread_only)

        controls.addStretch()
        layout.addLayout(controls)

        # --- Tree ---
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Article", "Author", "Page", "Rating"])
        self._tree.setColumnWidth(0, 260)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self._tree, stretch=3)

        # --- Article detail below tree ---
        self._detail = ArticleDetail(self)
        self._detail.article_changed.connect(self.refresh)
        layout.addWidget(self._detail, stretch=2)

    def refresh(self):
        """Reload the tree from the database."""
        self._tree.clear()

        session = get_session()
        try:
            magazines = session.query(Magazine).order_by(Magazine.title, Magazine.year.desc()).all()
            unread_only = self._unread_only.isChecked()

            for mag in magazines:
                articles = mag.articles
                if unread_only:
                    articles = [a for a in articles if not a.is_read]
                if not articles:
                    continue

                # Level 1 — magazine title
                mag_item = QTreeWidgetItem(self._tree, [mag.title])
                mag_item.setData(0, Qt.ItemDataRole.UserRole, ("magazine", mag.id))

                # Level 2 — issue
                issue_label = " — ".join(filter(None, [
                    f"Vol {mag.volume}" if mag.volume else None,
                    f"No {mag.issue_number}" if mag.issue_number else None,
                    mag.season,
                    str(mag.year) if mag.year else None,
                ]))
                issue_item = QTreeWidgetItem(mag_item, [issue_label or "Unknown Issue"])
                issue_item.setData(0, Qt.ItemDataRole.UserRole, ("issue", mag.id))

                # Level 3 — articles
                for article in articles:
                    read_icon = "✓" if article.is_read else "●"
                    stars = "★" * article.rating + "☆" * (5 - article.rating)
                    art_item = QTreeWidgetItem(issue_item, [
                        f"{read_icon} {article.title}",
                        article.author or "",
                        str(article.page_start) if article.page_start else "",
                        stars,
                    ])
                    art_item.setData(0, Qt.ItemDataRole.UserRole, ("article", article.id))

                issue_item.setExpanded(True)
                mag_item.setExpanded(True)
        finally:
            session.close()

    def _on_selection(self):
        items = self._tree.selectedItems()
        if not items:
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == "article":
            article_id = data[1]
            self._detail.load_article(article_id)
            self.article_selected.emit(article_id)

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "article":
            return

        article_id = data[1]
        menu = QMenu(self)

        session = get_session()
        try:
            article = session.get(Article, article_id)
            is_read = bool(article.is_read)
        finally:
            session.close()

        toggle_label = "Mark as Unread" if is_read else "Mark as Read"
        toggle_action = QAction(toggle_label, self)
        toggle_action.triggered.connect(lambda: self._toggle_read(article_id, not is_read))
        menu.addAction(toggle_action)

        open_action = QAction("Open to This Page", self)
        open_action.triggered.connect(lambda: self.article_selected.emit(article_id))
        menu.addAction(open_action)

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _toggle_read(self, article_id: int, is_read: bool):
        session = get_session()
        try:
            article = session.get(Article, article_id)
            article.is_read = int(is_read)
            session.commit()
        finally:
            session.close()
        self.refresh()

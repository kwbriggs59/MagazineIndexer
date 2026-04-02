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
    QInputDialog, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QAction

from database.db import get_session
from database.models import Magazine, Article
from ui.article_detail import ArticleDetail


class LibraryPanel(QWidget):
    article_selected = pyqtSignal(int)   # article_id
    reimport_requested = pyqtSignal(str) # pdf_path
    magazine_deleted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Filter / sort controls ---
        controls = QHBoxLayout()

        controls.addWidget(QLabel("Magazine:"))
        self._pub_filter = QComboBox()
        self._pub_filter.addItem("All")
        self._pub_filter.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self._pub_filter)

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
            # --- Repopulate publication filter, preserving selection ---
            current_pub = self._pub_filter.currentText()
            # Temporarily disconnect to prevent recursive refresh
            self._pub_filter.blockSignals(True)
            self._pub_filter.clear()
            self._pub_filter.addItem("All")
            pubs = (
                session.query(Magazine.publication)
                .filter(Magazine.publication.isnot(None))
                .distinct()
                .order_by(Magazine.publication)
                .all()
            )
            for (pub_name,) in pubs:
                if pub_name:
                    self._pub_filter.addItem(pub_name)
            # Restore previous selection
            idx = self._pub_filter.findText(current_pub)
            self._pub_filter.setCurrentIndex(idx if idx >= 0 else 0)
            self._pub_filter.blockSignals(False)

            selected_pub = self._pub_filter.currentText()

            # --- Query magazines with optional publication filter ---
            query = session.query(Magazine)
            if selected_pub and selected_pub != "All":
                query = query.filter(Magazine.publication == selected_pub)
            magazines = query.order_by(Magazine.title, Magazine.year.desc()).all()

            unread_only = self._unread_only.isChecked()

            for mag in magazines:
                articles = sorted(mag.articles,
                                  key=lambda a: (a.page_start is None, a.page_start or 0))
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
        if not data:
            return

        menu = QMenu(self)

        if data[0] == "issue":
            mag_id = data[1]
            offset_action = QAction("Set Page Offset…", self)
            offset_action.triggered.connect(lambda: self._set_page_offset(mag_id))
            menu.addAction(offset_action)

            reimport_action = QAction("Re-import Issue…", self)
            reimport_action.triggered.connect(lambda: self._reimport_magazine(mag_id))
            menu.addAction(reimport_action)

            menu.addSeparator()

            delete_action = QAction("Delete Issue…", self)
            delete_action.triggered.connect(lambda: self._delete_magazine(mag_id))
            menu.addAction(delete_action)

        elif data[0] == "article":
            article_id = data[1]

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

        if not menu.isEmpty():
            menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _set_page_offset(self, mag_id: int):
        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            current = mag.page_offset or 0
        finally:
            session.close()

        value, ok = QInputDialog.getInt(
            self,
            "Set Page Offset",
            "Number of unnumbered pages before page 1\n"
            "(auto-detected on import — adjust if articles open to the wrong page)",
            current, 0, 50,
        )
        if not ok:
            return

        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            mag.page_offset = value
            session.commit()
        finally:
            session.close()

    def _toggle_read(self, article_id: int, is_read: bool):
        session = get_session()
        try:
            article = session.get(Article, article_id)
            article.is_read = int(is_read)
            session.commit()
        finally:
            session.close()
        self.refresh()

    def _delete_magazine(self, mag_id: int):
        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            if mag is None:
                return
            label = " — ".join(filter(None, [
                mag.publication, mag.season, str(mag.year) if mag.year else None
            ])) or mag.title
        finally:
            session.close()

        reply = QMessageBox.question(
            self,
            "Delete Issue",
            f"Delete '{label}' and all its articles from the library?\n\nThis does not delete the PDF file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            if mag:
                session.query(Article).filter(Article.magazine_id == mag_id).delete()
                session.delete(mag)
                session.commit()
        finally:
            session.close()

        self.magazine_deleted.emit()
        self.refresh()

    def _reimport_magazine(self, mag_id: int):
        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            if mag is None:
                return
            pdf_path = mag.pdf_path
            label = " — ".join(filter(None, [
                mag.publication, mag.season, str(mag.year) if mag.year else None
            ])) or mag.title
        finally:
            session.close()

        if not pdf_path:
            QMessageBox.information(
                self,
                "Catalog-Only Entry",
                "This is a catalog-only entry with no PDF. Nothing to re-import.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Re-import Issue",
            f"Re-import '{label}'?\n\nThe existing record and articles will be deleted and re-extracted from the PDF.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            if mag:
                session.query(Article).filter(Article.magazine_id == mag_id).delete()
                session.delete(mag)
                session.commit()
        finally:
            session.close()

        self.reimport_requested.emit(pdf_path)
        self.refresh()

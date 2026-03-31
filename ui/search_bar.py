"""
Search Bar widget — FTS5-powered search with debounce and results dropdown.

Emits article_selected(article_id) when the user clicks a result.
Supports a collapsible Advanced Search panel for filtered queries.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QGroupBox, QFormLayout,
    QComboBox, QSpinBox,
)

from database.db import get_session
from sqlalchemy import text as sql_text


_FTS_QUERY = """
SELECT a.id, a.title, a.author, a.page_start,
       m.title as mag_title, m.volume, m.issue_number, m.year
FROM articles_fts
JOIN articles a ON articles_fts.rowid = a.id
JOIN magazines m ON a.magazine_id = m.id
WHERE articles_fts MATCH :q
ORDER BY rank
LIMIT 50
"""


class SearchBar(QWidget):
    article_selected = pyqtSignal(int)  # article_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._run_search)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Search box row
        row = QHBoxLayout()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search articles…")
        self._search_input.setMinimumWidth(300)
        self._search_input.textChanged.connect(self._debounce.start)
        self._search_input.installEventFilter(self)
        row.addWidget(self._search_input)

        self._adv_btn = QPushButton("▼")
        self._adv_btn.setFixedWidth(28)
        self._adv_btn.setCheckable(True)
        self._adv_btn.toggled.connect(self._toggle_advanced)
        row.addWidget(self._adv_btn)

        layout.addLayout(row)

        # Results dropdown
        self._results = QListWidget()
        self._results.setFixedHeight(200)
        self._results.hide()
        self._results.itemClicked.connect(self._on_result_click)
        layout.addWidget(self._results)

        # Advanced search panel
        self._adv_panel = QGroupBox("Advanced Search")
        self._adv_panel.hide()
        adv_form = QFormLayout(self._adv_panel)

        self._adv_title = QLineEdit()
        adv_form.addRow("Title:", self._adv_title)

        self._adv_author = QLineEdit()
        adv_form.addRow("Author:", self._adv_author)

        self._adv_tag = QLineEdit()
        adv_form.addRow("Tag:", self._adv_tag)

        self._adv_read = QComboBox()
        self._adv_read.addItems(["Any", "Read", "Unread"])
        adv_form.addRow("Read Status:", self._adv_read)

        self._adv_rating = QSpinBox()
        self._adv_rating.setRange(0, 5)
        self._adv_rating.setPrefix("≥ ")
        adv_form.addRow("Min Rating:", self._adv_rating)

        adv_search_btn = QPushButton("Search")
        adv_search_btn.clicked.connect(self._run_advanced)
        adv_form.addRow("", adv_search_btn)

        layout.addWidget(self._adv_panel)

    def _toggle_advanced(self, checked: bool):
        self._adv_panel.setVisible(checked)

    def _run_search(self):
        q = self._search_input.text().strip()
        if not q:
            self._results.hide()
            return

        session = get_session()
        try:
            rows = session.execute(sql_text(_FTS_QUERY), {"q": q}).fetchall()
        except Exception:
            rows = []
        finally:
            session.close()

        self._show_results(rows)

    def _run_advanced(self):
        session = get_session()
        try:
            from database.models import Article, Magazine
            from sqlalchemy.orm import Query
            q: Query = (
                session.query(Article, Magazine)
                .join(Magazine)
            )

            if self._adv_title.text():
                q = q.filter(Article.title.ilike(f"%{self._adv_title.text()}%"))
            if self._adv_author.text():
                q = q.filter(Article.author.ilike(f"%{self._adv_author.text()}%"))
            if self._adv_tag.text():
                q = q.filter(Article.keywords.ilike(f"%{self._adv_tag.text()}%"))
            if self._adv_read.currentText() == "Read":
                q = q.filter(Article.is_read == 1)
            elif self._adv_read.currentText() == "Unread":
                q = q.filter(Article.is_read == 0)
            if self._adv_rating.value() > 0:
                q = q.filter(Article.rating >= self._adv_rating.value())

            rows = [
                (a.id, a.title, a.author, a.page_start,
                 m.title, m.volume, m.issue_number, m.year)
                for a, m in q.limit(50).all()
            ]
        finally:
            session.close()

        self._show_results(rows)

    def _show_results(self, rows):
        self._results.clear()
        for row in rows:
            aid, title, author, page, mag, vol, issue, year = row
            parts = [title]
            if author:
                parts.append(f"· {author}")
            parts.append(f"· {mag}")
            if vol:
                parts.append(f"Vol {vol}")
            if page:
                parts.append(f"p.{page}")
            item = QListWidgetItem(" ".join(parts))
            item.setData(Qt.ItemDataRole.UserRole, aid)
            self._results.addItem(item)

        self._results.setVisible(bool(rows))

    def _on_result_click(self, item: QListWidgetItem):
        article_id = item.data(Qt.ItemDataRole.UserRole)
        self._results.hide()
        self._search_input.clear()
        self.article_selected.emit(article_id)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self._search_input and event.type() == QEvent.Type.KeyPress:
            from PyQt6.QtCore import Qt as _Qt
            if event.key() == _Qt.Key.Key_Escape:
                self._results.hide()
                self._search_input.clear()
        return super().eventFilter(obj, event)

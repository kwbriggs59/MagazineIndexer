"""
Article Detail Panel — displayed below the Library Panel tree.

Shows full metadata for the selected article and allows inline editing.
Emits article_changed() after any save so the Library Panel can refresh.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QTextEdit,
    QPushButton, QHBoxLayout, QVBoxLayout, QLabel,
    QSpinBox,
)
from PyQt6.QtCore import pyqtSignal

from database.db import get_session
from database.models import Article


class StarRating(QWidget):
    """Clickable 5-star rating widget."""
    rating_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rating = 0
        self._stars: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for i in range(1, 6):
            lbl = QLabel("☆")
            lbl.setStyleSheet("font-size: 18px; cursor: pointer;")
            # Capture loop variable
            lbl.mousePressEvent = lambda _, n=i: self._set(n)
            layout.addWidget(lbl)
            self._stars.append(lbl)
        layout.addStretch()

    def set_rating(self, rating: int):
        self._rating = rating
        for i, lbl in enumerate(self._stars):
            lbl.setText("★" if i < rating else "☆")

    def _set(self, n: int):
        self.set_rating(n)
        self.rating_changed.emit(n)


class ArticleDetail(QWidget):
    article_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._article_id: int | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        form = QFormLayout()

        self._title = QLineEdit()
        form.addRow("Title:", self._title)

        self._author = QLineEdit()
        form.addRow("Author:", self._author)

        pages_row = QHBoxLayout()
        self._page_start = QSpinBox()
        self._page_start.setRange(0, 9999)
        pages_row.addWidget(self._page_start)
        pages_row.addWidget(QLabel("–"))
        self._page_end = QSpinBox()
        self._page_end.setRange(0, 9999)
        pages_row.addWidget(self._page_end)
        pages_row.addStretch()
        form.addRow("Pages:", pages_row)

        self._keywords = QLineEdit()
        self._keywords.setPlaceholderText("comma-separated tags")
        form.addRow("Tags:", self._keywords)

        self._stars = StarRating()
        form.addRow("Rating:", self._stars)

        self._notes = QTextEdit()
        self._notes.setFixedHeight(70)
        form.addRow("Notes:", self._notes)

        layout.addLayout(form)

        # Action buttons
        btn_row = QHBoxLayout()

        self._read_btn = QPushButton("Mark as Read")
        self._read_btn.clicked.connect(self._toggle_read)
        btn_row.addWidget(self._read_btn)

        save_btn = QPushButton("Save Changes")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)
        self.setEnabled(False)

    def load_article(self, article_id: int):
        """Populate the form from the database."""
        self._article_id = article_id
        session = get_session()
        try:
            article = session.get(Article, article_id)
            if article is None:
                return
            self._title.setText(article.title or "")
            self._author.setText(article.author or "")
            self._page_start.setValue(article.page_start or 0)
            self._page_end.setValue(article.page_end or 0)
            self._keywords.setText(article.keywords or "")
            self._stars.set_rating(article.rating or 0)
            self._notes.setPlainText(article.notes or "")
            self._read_btn.setText(
                "Mark as Unread" if article.is_read else "Mark as Read"
            )
        finally:
            session.close()
        self.setEnabled(True)

    def _save(self):
        if self._article_id is None:
            return
        session = get_session()
        try:
            article = session.get(Article, self._article_id)
            article.title = self._title.text().strip()
            article.author = self._author.text().strip() or None
            article.page_start = self._page_start.value() or None
            article.page_end = self._page_end.value() or None
            article.keywords = self._keywords.text().strip() or None
            article.rating = self._stars._rating
            article.notes = self._notes.toPlainText().strip() or None
            session.commit()
        finally:
            session.close()
        self.article_changed.emit()

    def _toggle_read(self):
        if self._article_id is None:
            return
        session = get_session()
        try:
            article = session.get(Article, self._article_id)
            article.is_read = 0 if article.is_read else 1
            session.commit()
            self._read_btn.setText(
                "Mark as Unread" if article.is_read else "Mark as Read"
            )
        finally:
            session.close()
        self.article_changed.emit()

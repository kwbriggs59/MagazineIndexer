"""
Add Book / Article dialog.

Lets the user import a PDF as a book (with optional TOC chapter extraction)
or as an individual article (single-entry record).
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QDialogButtonBox, QFileDialog, QMessageBox,
)

from database.models import Magazine


class AddDocumentDialog(QDialog):
    """Modal dialog for importing a book or individual article PDF."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Book or Article")
        self.setMinimumWidth(480)
        self._result_mag: Magazine | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # File path row
        path_row = QHBoxLayout()
        self._path_field = QLineEdit()
        self._path_field.setReadOnly(True)
        self._path_field.setPlaceholderText("Select a PDF file…")
        path_row.addWidget(self._path_field)
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        form.addRow("PDF file:", path_row)

        # Type selector
        self._type_combo = QComboBox()
        self._type_combo.addItems(["Book", "Article"])
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Type:", self._type_combo)

        # Title
        self._title_field = QLineEdit()
        self._title_field.setPlaceholderText("Enter a title…")
        form.addRow("Title:", self._title_field)

        # Author
        self._author_field = QLineEdit()
        self._author_field.setPlaceholderText("Optional")
        form.addRow("Author:", self._author_field)

        # Extract TOC checkbox (books only)
        self._extract_toc_check = QCheckBox("Extract chapters from table of contents")
        self._extract_toc_check.setChecked(True)
        self._extract_toc_check.setToolTip(
            "Scan the PDF for a table of contents and create chapter entries.\n"
            "Uncheck to import without chapter extraction (faster)."
        )
        form.addRow("", self._extract_toc_check)

        layout.addLayout(form)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self._on_import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        self._path_field.setText(path)
        # Pre-fill title from filename if the field is empty
        if not self._title_field.text().strip():
            name = os.path.splitext(os.path.basename(path))[0]
            self._title_field.setText(name)

    def _on_type_changed(self):
        is_book = self._type_combo.currentText() == "Book"
        self._extract_toc_check.setVisible(is_book)

    def _on_import(self):
        pdf_path = self._path_field.text().strip()
        title = self._title_field.text().strip()
        author = self._author_field.text().strip() or None
        doc_type = self._type_combo.currentText()

        if not pdf_path:
            QMessageBox.warning(self, "Missing File", "Please select a PDF file.")
            return
        if not title:
            QMessageBox.warning(self, "Missing Title", "Please enter a title.")
            return
        if not os.path.exists(pdf_path):
            QMessageBox.critical(self, "File Not Found", f"File not found:\n{pdf_path}")
            return

        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            if doc_type == "Book":
                from core.scanner import import_book
                self._result_mag = import_book(
                    pdf_path=pdf_path,
                    title=title,
                    author=author,
                    extract_toc=self._extract_toc_check.isChecked(),
                )
            else:
                from core.scanner import import_article
                self._result_mag = import_article(
                    pdf_path=pdf_path,
                    title=title,
                    author=author,
                )
        except Exception as exc:
            self.unsetCursor()
            QMessageBox.critical(self, "Import Failed", str(exc))
            return

        self.unsetCursor()
        self.accept()

    # ------------------------------------------------------------------
    # Result accessor
    # ------------------------------------------------------------------

    def result_magazine(self) -> Magazine | None:
        """Returns the newly imported Magazine record, or None if cancelled."""
        return self._result_mag

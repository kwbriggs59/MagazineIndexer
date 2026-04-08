"""
Main application window.

Layout:
  - Two-row toolbar:
      Row 1: "Folder:" label | path field (read-only) | Browse button | Scan Now button
      Row 2: SearchBar widget | WCI Index | Import Index | Settings
  - Central QStackedWidget:
      Index 0: MagazineGrid  (home screen — thumbnail grid)
      Index 1: QSplitter (TocPanel left | ReaderPanel right)

Navigation:
  - Clicking a magazine card → load TOC + open PDF → switch to index 1
  - Clicking "← Back" in TocPanel → switch to index 0, refresh grid
  - Clicking an article in TocPanel → navigate ReaderPanel to that page
  - SearchBar article_selected → load TOC for that magazine + open article → switch to index 1
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QStackedWidget, QToolBar, QLabel,
    QLineEdit, QPushButton, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt

from database.db import init_db, get_session, get_setting, set_setting
from database.models import Article, Magazine
from ui.magazine_grid import MagazineGrid
from ui.toc_panel import TocPanel
from ui.reader_panel import ReaderPanel
from ui.search_bar import SearchBar
from ui.settings_dialog import SettingsDialog
from ui.import_dialog import ImportDialog
from ui.wci_index_panel import WciIndexPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        init_db()
        self.setWindowTitle("Magazine Library")
        self.resize(1280, 800)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        # --- Row 1 toolbar ---
        row1 = QToolBar("Folder")
        row1.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, row1)

        row1.addWidget(QLabel("Folder:"))
        self._folder_field = QLineEdit()
        self._folder_field.setReadOnly(True)
        self._folder_field.setMinimumWidth(400)
        self._folder_field.setPlaceholderText("No folder selected — click Browse")
        row1.addWidget(self._folder_field)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._on_browse)
        row1.addWidget(browse_btn)

        self._scan_btn = QPushButton("Scan Now")
        self._scan_btn.setEnabled(False)
        self._scan_btn.clicked.connect(self._on_scan)
        row1.addWidget(self._scan_btn)

        # --- Row 2 toolbar ---
        row2 = QToolBar("Search")
        row2.setMovable(False)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, row2)

        self._search_bar = SearchBar(self)
        row2.addWidget(self._search_bar)

        wci_index_btn = QPushButton("WCI Index")
        wci_index_btn.setToolTip("Browse the Woodcarving Illustrated master article index")
        wci_index_btn.clicked.connect(self._on_wci_index)
        row2.addWidget(wci_index_btn)

        add_doc_btn = QPushButton("Add Book/Article…")
        add_doc_btn.setToolTip("Import a PDF book or individual article into the library")
        add_doc_btn.clicked.connect(self._on_add_document)
        row2.addWidget(add_doc_btn)

        idx_btn = QPushButton("Import Index…")
        idx_btn.setToolTip("Import a master index PDF to populate article catalog without PDFs")
        idx_btn.clicked.connect(self._on_import_index)
        row2.addWidget(idx_btn)

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self._on_settings)
        row2.addWidget(settings_btn)

        self._wci_index_window: WciIndexPanel | None = None

        # --- Central stacked widget ---
        # Page 0: magazine grid (home screen)
        self._magazine_grid = MagazineGrid(self)

        # Page 1: TOC panel + PDF reader
        self._toc_panel = TocPanel(self)
        self._reader_panel = ReaderPanel(self)
        reader_splitter = QSplitter(Qt.Orientation.Horizontal)
        reader_splitter.addWidget(self._toc_panel)
        reader_splitter.addWidget(self._reader_panel)
        reader_splitter.setSizes([280, 1000])

        self._stack = QStackedWidget()
        self._stack.addWidget(self._magazine_grid)   # index 0
        self._stack.addWidget(reader_splitter)        # index 1
        self.setCentralWidget(self._stack)

        # --- Signal wiring ---
        # Grid → reader mode
        self._magazine_grid.magazine_selected.connect(self._on_magazine_selected)
        self._magazine_grid.reimport_requested.connect(self._on_reimport)
        self._magazine_grid.delete_requested.connect(self._on_delete_magazine)

        # TOC panel → reader + back
        self._toc_panel.article_selected.connect(self._reader_panel.open_article)
        self._toc_panel.back_requested.connect(self._on_back)

        # Search bar → reader (must load TOC first)
        self._search_bar.article_selected.connect(self._on_search_article_selected)

    def _load_settings(self):
        folder = get_setting("watched_folder", "")
        if folder:
            self._folder_field.setText(folder)
            self._scan_btn.setEnabled(True)

    # --- Toolbar handlers ---

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Magazine Folder")
        if folder:
            set_setting("watched_folder", folder)
            self._folder_field.setText(folder)
            self._scan_btn.setEnabled(True)

    def _on_scan(self):
        folder = get_setting("watched_folder", "")
        if not folder:
            QMessageBox.warning(self, "No Folder", "Please select a watched folder first.")
            return

        dialog = ImportDialog(folder, parent=self)
        dialog.exec()
        self._magazine_grid.refresh()
        if self._wci_index_window is not None:
            self._wci_index_window.refresh()

    def _on_wci_index(self):
        if self._wci_index_window is None:
            self._wci_index_window = WciIndexPanel()
        self._wci_index_window.show()
        self._wci_index_window.raise_()
        self._wci_index_window.activateWindow()

    def _on_add_document(self):
        from ui.add_document_dialog import AddDocumentDialog
        dlg = AddDocumentDialog(self)
        if dlg.exec() == AddDocumentDialog.DialogCode.Accepted:
            self._magazine_grid.refresh()

    def _on_import_index(self):
        from PyQt6.QtWidgets import QInputDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Index PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        pub, ok = QInputDialog.getText(
            self,
            "Publication Name",
            "Magazine series name (e.g. 'Wildfowl Carving'):",
        )
        if not ok or not pub.strip():
            return

        from core.scanner import import_index
        try:
            count = import_index(path, pub.strip())
            QMessageBox.information(
                self, "Index Imported", f"{count} articles imported."
            )
            self._magazine_grid.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()
        self._load_settings()

    # --- Navigation handlers ---

    def _on_magazine_selected(self, mag_id: int):
        """Open reader mode for the selected magazine."""
        self._toc_panel.load_magazine(mag_id)
        self._reader_panel.open_magazine(mag_id)
        self._stack.setCurrentIndex(1)

    def _on_back(self):
        """Return to the magazine grid home screen."""
        self._stack.setCurrentIndex(0)

    def _on_search_article_selected(self, article_id: int):
        """Open reader mode for the magazine that owns the searched article."""
        session = get_session()
        try:
            article = session.get(Article, article_id)
            if article is None:
                return
            mag_id = article.magazine_id
        finally:
            session.close()

        self._toc_panel.load_magazine(mag_id)
        self._reader_panel.open_article(article_id)
        self._stack.setCurrentIndex(1)

    # --- Magazine management handlers ---

    def _on_reimport(self, pdf_path: str):
        """Delete the existing record and re-import the PDF."""
        folder = get_setting("watched_folder", "")
        dialog = ImportDialog(folder, parent=self, pdf_paths=[pdf_path])
        dialog.exec()
        self._magazine_grid.refresh()

    def _on_delete_magazine(self, mag_id: int):
        """Confirm and delete a magazine + all its articles from the DB."""
        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            if mag is None:
                return
            label = " — ".join(
                p for p in [mag.publication, mag.season, str(mag.year) if mag.year else None]
                if p
            ) or mag.title
        finally:
            session.close()

        reply = QMessageBox.question(
            self,
            "Delete Issue",
            f"Delete '{label}' and all its articles from the library?\n\n"
            "This does not delete the PDF file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        session = get_session()
        try:
            mag = session.get(Magazine, mag_id)
            if mag:
                session.delete(mag)
                session.commit()
        finally:
            session.close()

        self._magazine_grid.refresh()

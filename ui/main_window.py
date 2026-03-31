"""
Main application window.

Layout:
  - Two-row toolbar:
      Row 1: "Folder:" label | path field (read-only) | Browse button | Scan Now button
      Row 2: SearchBar widget | Settings button
  - Horizontal QSplitter: LibraryPanel (left) | ReaderPanel (right)

Signals connected here:
  - Browse → QFileDialog → save to DB → update path display → enable Scan Now
  - Scan Now → scanner.scan_directory → ImportDialog
  - Settings → SettingsDialog
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QToolBar, QLabel,
    QLineEdit, QPushButton, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt

from database.db import init_db, get_setting, set_setting
from ui.library_panel import LibraryPanel
from ui.reader_panel import ReaderPanel
from ui.search_bar import SearchBar
from ui.settings_dialog import SettingsDialog
from ui.import_dialog import ImportDialog


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

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self._on_settings)
        row2.addWidget(settings_btn)

        # --- Central splitter ---
        self._library_panel = LibraryPanel(self)
        self._reader_panel = ReaderPanel(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._library_panel)
        splitter.addWidget(self._reader_panel)
        splitter.setSizes([350, 930])
        self.setCentralWidget(splitter)

        # Wire article selection
        self._library_panel.article_selected.connect(self._reader_panel.open_article)

    def _load_settings(self):
        folder = get_setting("watched_folder", "")
        if folder:
            self._folder_field.setText(folder)
            self._scan_btn.setEnabled(True)

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
        self._library_panel.refresh()

    def _on_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()
        self._load_settings()

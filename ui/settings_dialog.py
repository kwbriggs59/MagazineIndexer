"""
Settings Dialog — tabbed dialog for all user-configurable settings.

Tab 1 — General:
  Watched folder (path + Browse), theme, confirm-before-AI toggle

Tab 2 — OCR & AI:
  Anthropic API key, OCR confidence threshold slider,
  OCR DPI dropdown, OCR language, View AI log button, Clear Database button
"""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QFileDialog, QSlider, QLabel,
    QHBoxLayout, QComboBox, QRadioButton, QButtonGroup,
    QMessageBox, QDialogButtonBox,
)

import config
from database.db import get_setting, set_setting, get_session
from database.models import Magazine, Article


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(480, 380)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_ocr_tab(), "OCR & AI")
        tabs.addTab(self._build_remote_tab(), "Remote Database")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_general_tab(self) -> QWidget:
        widget = QWidget()
        layout = QFormLayout(widget)

        folder_row = QHBoxLayout()
        self._folder_field = QLineEdit()
        self._folder_field.setReadOnly(True)
        folder_row.addWidget(self._folder_field)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._on_browse)
        folder_row.addWidget(browse_btn)
        layout.addRow("Watched Folder:", folder_row)

        theme_row = QHBoxLayout()
        self._theme_light = QRadioButton("Light")
        self._theme_dark = QRadioButton("Dark")
        self._theme_group = QButtonGroup()
        self._theme_group.addButton(self._theme_light, 0)
        self._theme_group.addButton(self._theme_dark, 1)
        theme_row.addWidget(self._theme_light)
        theme_row.addWidget(self._theme_dark)
        theme_row.addStretch()
        layout.addRow("Theme:", theme_row)

        ai_row = QHBoxLayout()
        self._ask_ai_yes = QRadioButton("Yes (default)")
        self._ask_ai_no = QRadioButton("No — always use AI when needed")
        self._ask_ai_group = QButtonGroup()
        self._ask_ai_group.addButton(self._ask_ai_yes, 0)
        self._ask_ai_group.addButton(self._ask_ai_no, 1)
        ai_row.addWidget(self._ask_ai_yes)
        ai_row.addWidget(self._ask_ai_no)
        ai_row.addStretch()
        layout.addRow("Confirm before AI:", ai_row)

        return widget

    def _build_ocr_tab(self) -> QWidget:
        widget = QWidget()
        layout = QFormLayout(widget)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-ant-…")
        layout.addRow("Anthropic API Key:", self._api_key)

        threshold_row = QHBoxLayout()
        self._threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._threshold_slider.setRange(50, 90)
        self._threshold_slider.setTickInterval(5)
        self._threshold_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._threshold_label = QLabel("70")
        self._threshold_slider.valueChanged.connect(
            lambda v: self._threshold_label.setText(
                f"{v} — use AI if OCR confidence is below this"
            )
        )
        threshold_row.addWidget(self._threshold_slider)
        threshold_row.addWidget(self._threshold_label)
        layout.addRow("OCR Confidence Threshold:", threshold_row)

        self._dpi_combo = QComboBox()
        self._dpi_combo.addItems(["150", "200", "300", "400"])
        layout.addRow("OCR Resolution (DPI):", self._dpi_combo)

        self._ocr_lang = QLineEdit()
        self._ocr_lang.setPlaceholderText("eng")
        layout.addRow("OCR Language:", self._ocr_lang)

        view_log_btn = QPushButton("View AI Usage Log")
        view_log_btn.clicked.connect(self._view_log)
        layout.addRow("", view_log_btn)

        clear_db_btn = QPushButton("Clear Database…")
        clear_db_btn.setStyleSheet("color: red;")
        clear_db_btn.clicked.connect(self._clear_database)
        layout.addRow("", clear_db_btn)

        return widget

    def _build_remote_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        form = QFormLayout()

        path_row = QHBoxLayout()
        self._remote_path = QLineEdit()
        self._remote_path.setPlaceholderText("e.g. G:\\My Drive\\Magazines\\magazine_library.db")
        path_row.addWidget(self._remote_path)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_remote)
        path_row.addWidget(browse_btn)
        form.addRow("Remote DB Path:", path_row)

        self._last_sync_label = QLabel("Never")
        self._last_sync_label.setStyleSheet("color: #666;")
        form.addRow("Last Sync:", self._last_sync_label)

        layout.addLayout(form)
        layout.addSpacing(12)

        sync_btn = QPushButton("Sync Local → Remote")
        sync_btn.setFixedWidth(200)
        sync_btn.clicked.connect(self._on_sync)
        layout.addWidget(sync_btn)

        return widget

    def _load(self):
        self._folder_field.setText(get_setting("watched_folder", ""))

        theme = get_setting("theme", "light")
        if theme == "dark":
            self._theme_dark.setChecked(True)
        else:
            self._theme_light.setChecked(True)

        if get_setting("ask_before_ai", "true") == "false":
            self._ask_ai_no.setChecked(True)
        else:
            self._ask_ai_yes.setChecked(True)

        self._api_key.setText(get_setting("anthropic_api_key", ""))

        threshold = int(get_setting("ocr_confidence_threshold", "70"))
        self._threshold_slider.setValue(threshold)

        dpi = get_setting("ocr_dpi", "300")
        idx = self._dpi_combo.findText(dpi)
        if idx >= 0:
            self._dpi_combo.setCurrentIndex(idx)

        self._ocr_lang.setText(get_setting("ocr_language", "eng"))

        self._remote_path.setText(get_setting("remote_db_path", ""))
        last_sync = get_setting("remote_db_last_sync", "")
        self._last_sync_label.setText(last_sync if last_sync else "Never")

    def _save(self):
        set_setting("watched_folder", self._folder_field.text())
        set_setting("theme", "dark" if self._theme_dark.isChecked() else "light")
        set_setting("ask_before_ai", "false" if self._ask_ai_no.isChecked() else "true")
        set_setting("anthropic_api_key", self._api_key.text().strip())
        set_setting("ocr_confidence_threshold", str(self._threshold_slider.value()))
        set_setting("ocr_dpi", self._dpi_combo.currentText())
        set_setting("ocr_language", self._ocr_lang.text().strip() or "eng")
        set_setting("remote_db_path", self._remote_path.text().strip())
        self.accept()

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Magazine Folder")
        if folder:
            self._folder_field.setText(folder)

    def _on_browse_remote(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Remote Database Location",
            self._remote_path.text() or "magazine_library.db",
            "SQLite Database (*.db)",
        )
        if path:
            self._remote_path.setText(path)

    def _on_sync(self):
        remote_path = self._remote_path.text().strip()
        if not remote_path:
            QMessageBox.warning(self, "No Remote Path", "Set a remote DB path first.")
            return

        import os
        remote_exists = os.path.exists(remote_path)

        reply = QMessageBox.question(
            self,
            "Sync Database",
            f"Copy local database to:\n{remote_path}\n\n"
            + ("Server edits (read status, ratings, notes) will be merged first.\n\n"
               if remote_exists else "")
            + "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            if remote_exists:
                self._merge_from_remote(remote_path)
            # Use SQLite's online backup API instead of shutil.copy2 — shutil
            # copies only the main .db file and misses any in-flight WAL pages,
            # producing a "database disk image is malformed" error on the Pi.
            # sqlite3.connect().backup() checkpoints the WAL and copies atomically.
            src = sqlite3.connect(config.DB_PATH)
            dst = sqlite3.connect(remote_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            set_setting("remote_db_path", remote_path)
            set_setting("remote_db_last_sync", timestamp)
            self._last_sync_label.setText(timestamp)
            QMessageBox.information(self, "Sync Complete", "Database synced successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Sync Failed", str(e))

    def _merge_from_remote(self, remote_path: str) -> None:
        """Pull server-editable fields from the remote DB into the local DB before overwriting."""
        remote = sqlite3.connect(remote_path)
        try:
            # Articles: pull all fields the server can write
            rows = remote.execute(
                "SELECT id, is_read, rating, title, author, "
                "page_start, page_end, keywords, notes FROM articles"
            ).fetchall()
            # Magazines: pull page_offset (adjustable from the server reader)
            mag_rows = remote.execute(
                "SELECT id, page_offset FROM magazines"
            ).fetchall()
        finally:
            remote.close()

        local = sqlite3.connect(config.DB_PATH)
        try:
            local.executemany(
                "UPDATE articles SET is_read=?, rating=?, title=?, author=?, "
                "page_start=?, page_end=?, keywords=?, notes=? WHERE id=?",
                [(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[0]) for r in rows],
            )
            local.executemany(
                "UPDATE magazines SET page_offset=? WHERE id=?",
                [(r[1], r[0]) for r in mag_rows],
            )
            local.commit()
        finally:
            local.close()

    def _view_log(self):
        try:
            subprocess.Popen(["notepad.exe", config.AI_USAGE_LOG])
        except FileNotFoundError:
            QMessageBox.information(self, "Log", f"Log file: {config.AI_USAGE_LOG}")

    def _clear_database(self):
        reply = QMessageBox.question(
            self,
            "Clear Database",
            "This will permanently delete all magazines and articles.\n"
            "Your settings will be kept. Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            session = get_session()
            try:
                session.query(Article).delete()
                session.query(Magazine).delete()
                session.commit()
            finally:
                session.close()
            QMessageBox.information(self, "Done", "Database cleared.")

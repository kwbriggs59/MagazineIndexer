"""
Import Dialog — modal progress dialog shown while scanning and importing magazines.

Runs the import pipeline in a background QThread so the GUI stays responsive.
Handles AI confirmation prompts via a thread-safe signal/slot mechanism.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar,
    QTextEdit, QPushButton, QHBoxLayout, QWidget,
    QMessageBox,
)

from database.db import get_setting
from core.scanner import scan_directory, import_magazine


class _ImportWorker(QThread):
    progress = pyqtSignal(str)          # status message
    pdf_started = pyqtSignal(str, int, int)  # filename, current, total
    pdf_done = pyqtSignal(str)
    finished = pyqtSignal(int, int)     # new_magazines, new_articles
    ai_confirm_needed = pyqtSignal(str) # filename — worker blocks until responded
    error = pyqtSignal(str)

    def __init__(self, folder: str, parent=None):
        super().__init__(parent)
        self._folder = folder
        self._cancelled = False
        self._ai_response: bool | None = None
        self._ai_yes_to_all = False
        self._ai_no_to_all = False

    def cancel(self):
        self._cancelled = True

    def respond_ai(self, yes: bool):
        self._ai_response = yes

    def run(self):
        try:
            new_pdfs = scan_directory(self._folder)
        except Exception as e:
            self.error.emit(str(e))
            return

        total = len(new_pdfs)
        new_magazines = 0
        new_articles = 0
        api_key = get_setting("anthropic_api_key", "")
        ask = get_setting("ask_before_ai", "true") == "true"
        threshold = int(get_setting("ocr_confidence_threshold", "70"))
        dpi = int(get_setting("ocr_dpi", "300"))

        for i, pdf_path in enumerate(new_pdfs):
            if self._cancelled:
                break

            import os
            self.pdf_started.emit(os.path.basename(pdf_path), i + 1, total)

            def confirm_ai(filename: str) -> bool:
                if self._ai_yes_to_all:
                    return True
                if self._ai_no_to_all:
                    return False
                self._ai_response = None
                self.ai_confirm_needed.emit(filename)
                # Block until main thread responds
                while self._ai_response is None:
                    self.msleep(50)
                return self._ai_response

            try:
                mag = import_magazine(
                    pdf_path,
                    progress_callback=lambda msg: self.progress.emit(msg),
                    ocr_dpi=dpi,
                    confidence_threshold=threshold,
                    api_key=api_key or None,
                    ask_before_ai=ask,
                    ai_confirm_callback=confirm_ai if ask else None,
                )
                new_magazines += 1
                new_articles += len(mag.articles)
                self.pdf_done.emit(os.path.basename(pdf_path))
            except Exception as e:
                self.progress.emit(f"ERROR importing {os.path.basename(pdf_path)}: {e}")

        self.finished.emit(new_magazines, new_articles)


class ImportDialog(QDialog):
    def __init__(self, folder: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scanning for New Issues")
        self.setModal(True)
        self.resize(560, 400)
        self._folder = folder
        self._worker: _ImportWorker | None = None
        self._build_ui()
        self._start()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"Scanning folder: {self._folder}"))

        self._overall_bar = QProgressBar()
        self._overall_bar.setRange(0, 0)  # indeterminate until we know total
        layout.addWidget(self._overall_bar)

        self._status_label = QLabel("Starting…")
        layout.addWidget(self._status_label)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(150)
        layout.addWidget(self._log)

        # AI confirmation panel (hidden until needed)
        self._ai_panel = QWidget()
        ai_layout = QVBoxLayout(self._ai_panel)
        self._ai_label = QLabel()
        ai_layout.addWidget(self._ai_label)

        ai_btns = QHBoxLayout()
        for label, handler in [
            ("Yes", lambda: self._ai_respond(True, False, False)),
            ("No", lambda: self._ai_respond(False, False, False)),
            ("Yes to All", lambda: self._ai_respond(True, True, False)),
            ("No to All", lambda: self._ai_respond(False, False, True)),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            ai_btns.addWidget(btn)
        ai_layout.addLayout(ai_btns)
        self._ai_panel.hide()
        layout.addWidget(self._ai_panel)

        # Cancel / Done button
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _start(self):
        self._worker = _ImportWorker(self._folder, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.pdf_started.connect(self._on_pdf_started)
        self._worker.pdf_done.connect(self._on_pdf_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.ai_confirm_needed.connect(self._on_ai_confirm)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    @pyqtSlot(str)
    def _on_progress(self, msg: str):
        self._status_label.setText(msg)
        self._log.append(msg)

    @pyqtSlot(str, int, int)
    def _on_pdf_started(self, filename: str, current: int, total: int):
        self._overall_bar.setRange(0, total)
        self._overall_bar.setValue(current - 1)
        self._status_label.setText(f"Processing {filename} ({current}/{total})")

    @pyqtSlot(str)
    def _on_pdf_done(self, filename: str):
        val = self._overall_bar.value()
        self._overall_bar.setValue(val + 1)
        self._log.append(f"✓ {filename}")

    @pyqtSlot(int, int)
    def _on_finished(self, new_mags: int, new_articles: int):
        self._overall_bar.setValue(self._overall_bar.maximum())
        self._status_label.setText(
            f"{new_mags} new issue(s) found · {new_articles} articles indexed"
        )
        self._cancel_btn.setText("Done")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.accept)

    @pyqtSlot(str)
    def _on_ai_confirm(self, filename: str):
        self._ai_label.setText(
            f"OCR confidence is low for: {filename}\n"
            f"Use AI extraction? (~$0.001 per page)"
        )
        self._ai_panel.show()

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        self._log.append(f"ERROR: {msg}")

    def _ai_respond(self, yes: bool, yes_all: bool, no_all: bool):
        if self._worker:
            if yes_all:
                self._worker._ai_yes_to_all = True
            if no_all:
                self._worker._ai_no_to_all = True
            self._worker.respond_ai(yes)
        self._ai_panel.hide()

    def _on_cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
        self.reject()

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
        super().closeEvent(event)

"""
Woodcarving Illustrated Master Index Panel.

Displays all articles from the external CSV index with green highlighting for
issues the user has in their library.  Opened as a standalone non-modal window
via the "WCI Index" button in the main toolbar.

Ownership is detected from two sources (union):
  1. magazines.issue_number values in the database (PDFs already imported)
  2. A manual "wci_owned_manual" setting (comma-separated issue numbers) that
     the user can toggle via right-click — useful for physical copies not yet
     scanned.
"""

from __future__ import annotations

import csv
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QAction
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem,
    QLabel, QLineEdit, QPushButton,
    QHeaderView, QMenu,
)

import config
from database.db import get_session, get_setting, set_setting
from database.models import Magazine

# ── CSV column indices ────────────────────────────────────────────────────────
_C_TITLE     = 0
_C_CONTRIB1  = 2
_C_CONTRIB2  = 3
_C_CONTRIB3  = 4
_C_ISSUE_NUM = 5
_C_ISSUE_NAME = 6
_C_YEAR      = 7
_C_PAGE      = 8
_C_SUBJ1     = 9
_C_SUBJ2     = 10
_C_SUBJ3     = 11

# ── Colours ───────────────────────────────────────────────────────────────────
_GREEN   = QColor(0xC8, 0xE6, 0xC9)   # owned — has library entry
_DEFAULT = QColor(0xFF, 0xFF, 0xFF)


class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically when the text is a plain integer."""
    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return int(self.text()) < int(other.text())
        except ValueError:
            return super().__lt__(other)


def _load_csv() -> list[list[str]]:
    path = config.WCI_INDEX_CSV
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)          # skip header
        rows = []
        for row in reader:
            while len(row) < 15:   # pad short rows
                row.append("")
            rows.append(row)
    return rows


def _get_owned_issue_numbers() -> set[int]:
    """Union of DB-imported issue numbers and manually marked ones."""
    session = get_session()
    try:
        owned = {
            m.issue_number
            for m in session.query(Magazine).all()
            if m.issue_number is not None
        }
    finally:
        session.close()

    manual = get_setting("wci_owned_manual", "") or ""
    for part in manual.split(","):
        part = part.strip()
        if part.isdigit():
            owned.add(int(part))

    return owned


def _try_int(s: str) -> int:
    try:
        return int(s.strip())
    except ValueError:
        return -1


class WciIndexPanel(QWidget):
    """Non-modal window — Woodcarving Illustrated master article index."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Woodcarving Illustrated — Master Index")
        self.resize(1200, 750)

        self._all_rows: list[list[str]] = []
        self._owned: set[int] = set()
        self._filter_mode = "all"

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._apply_filters)

        self._build_ui()
        self._load_data()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Filter bar
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Show:"))

        self._btn_all     = QPushButton("All Issues")
        self._btn_owned   = QPushButton("Owned")
        self._btn_unowned = QPushButton("Unowned")
        for btn, mode in [
            (self._btn_all,     "all"),
            (self._btn_owned,   "owned"),
            (self._btn_unowned, "unowned"),
        ]:
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, m=mode: self._set_filter(m))
            bar.addWidget(btn)
        self._btn_all.setChecked(True)

        bar.addSpacing(16)
        bar.addWidget(QLabel("Search:"))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Title, author, subject…")
        self._search_box.setMinimumWidth(260)
        self._search_box.textChanged.connect(self._debounce.start)
        bar.addWidget(self._search_box)

        bar.addSpacing(16)
        self._count_label = QLabel()
        bar.addWidget(self._count_label)
        bar.addStretch()

        layout.addLayout(bar)

        # Table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Issue #", "Year", "Season", "Title", "Author", "Page", "Subjects"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)   # Title
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)   # Subjects
        self._table.setColumnWidth(0, 65)
        self._table.setColumnWidth(1, 55)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(4, 180)
        self._table.setColumnWidth(5, 55)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table)

        # Legend
        legend = QHBoxLayout()
        swatch = QLabel("   ")
        swatch.setAutoFillBackground(True)
        p = swatch.palette()
        p.setColor(swatch.backgroundRole(), _GREEN)
        swatch.setPalette(p)
        legend.addWidget(swatch)
        legend.addWidget(QLabel("= Issue in your library (right-click any row to manually mark ownership)"))
        legend.addStretch()
        layout.addLayout(legend)

    # ── Data ─────────────────────────────────────────────────────────────────

    def _load_data(self):
        self._all_rows = _load_csv()
        self._owned    = _get_owned_issue_numbers()
        self._apply_filters()

    def refresh(self):
        """Reload ownership from DB — call after a Scan Now completes."""
        self._owned = _get_owned_issue_numbers()
        self._apply_filters()

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _set_filter(self, mode: str):
        self._filter_mode = mode
        self._btn_all.setChecked(mode == "all")
        self._btn_owned.setChecked(mode == "owned")
        self._btn_unowned.setChecked(mode == "unowned")
        self._apply_filters()

    def _apply_filters(self):
        search = self._search_box.text().strip().lower()
        mode   = self._filter_mode
        owned  = self._owned

        self._table.setSortingEnabled(False)
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(0)

        visible_count = 0
        owned_issue_nums: set[int] = set()

        for row in self._all_rows:
            issue_num = _try_int(row[_C_ISSUE_NUM])
            is_owned  = issue_num in owned

            if issue_num > 0 and is_owned:
                owned_issue_nums.add(issue_num)

            if mode == "owned"   and not is_owned:
                continue
            if mode == "unowned" and is_owned:
                continue

            if search:
                haystack = " ".join([
                    row[_C_TITLE], row[_C_CONTRIB1], row[_C_CONTRIB2],
                    row[_C_SUBJ1], row[_C_SUBJ2], row[_C_SUBJ3],
                    row[_C_ISSUE_NAME],
                ]).lower()
                if search not in haystack:
                    continue

            r = self._table.rowCount()
            self._table.insertRow(r)
            self._populate_row(r, row, issue_num, is_owned)
            visible_count += 1

        self._table.setUpdatesEnabled(True)
        self._table.setSortingEnabled(True)

        total_issues = len({_try_int(r[_C_ISSUE_NUM]) for r in self._all_rows
                            if _try_int(r[_C_ISSUE_NUM]) > 0})
        self._count_label.setText(
            f"{visible_count} articles  |  "
            f"{len(owned_issue_nums)} of {total_issues} issues owned"
        )

    def _populate_row(self, r: int, row: list[str], issue_num: int, is_owned: bool):
        authors = ", ".join(filter(None, [
            row[_C_CONTRIB1].strip(),
            row[_C_CONTRIB2].strip(),
            row[_C_CONTRIB3].strip(),
        ]))
        subjects = " · ".join(filter(None, [
            row[_C_SUBJ1].strip(),
            row[_C_SUBJ2].strip(),
            row[_C_SUBJ3].strip(),
        ]))
        color = _GREEN if is_owned else _DEFAULT

        cells = [
            (row[_C_ISSUE_NUM].strip(), True),   # numeric sort
            (row[_C_YEAR].strip(),      False),
            (row[_C_ISSUE_NAME].strip(), False),
            (row[_C_TITLE].strip(),     False),
            (authors,                   False),
            (row[_C_PAGE].strip(),      False),
            (subjects,                  False),
        ]

        for col, (text, numeric) in enumerate(cells):
            item = _NumericItem(text) if numeric else QTableWidgetItem(text)
            item.setBackground(color)
            item.setData(Qt.ItemDataRole.UserRole, issue_num)
            self._table.setItem(r, col, item)

    # ── Context menu ─────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        issue_item = self._table.item(row, 0)
        if not issue_item:
            return
        issue_num = issue_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(issue_num, int) or issue_num <= 0:
            return

        is_owned = issue_num in self._owned

        # Can't remove DB-imported magazines from here — only toggle the manual list.
        # Figure out whether this issue is DB-owned or only manually marked.
        session = get_session()
        try:
            db_owned = any(
                m.issue_number == issue_num
                for m in session.query(Magazine).all()
            )
        finally:
            session.close()

        menu = QMenu(self)
        if is_owned and not db_owned:
            act = QAction(f"Remove Issue #{issue_num} from manual owned list", self)
            act.triggered.connect(lambda: self._set_manual_owned(issue_num, False))
            menu.addAction(act)
        elif not is_owned:
            act = QAction(f"Mark Issue #{issue_num} as owned (physical copy)", self)
            act.triggered.connect(lambda: self._set_manual_owned(issue_num, True))
            menu.addAction(act)
        else:
            # DB-owned: show info but no toggle (can't unimport from here)
            act = QAction(f"Issue #{issue_num} is in your PDF library", self)
            act.setEnabled(False)
            menu.addAction(act)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _set_manual_owned(self, issue_num: int, add: bool):
        manual = get_setting("wci_owned_manual", "") or ""
        nums = {int(x.strip()) for x in manual.split(",") if x.strip().isdigit()}
        if add:
            nums.add(issue_num)
        else:
            nums.discard(issue_num)
        set_setting("wci_owned_manual", ",".join(str(n) for n in sorted(nums)))
        self._owned = _get_owned_issue_numbers()
        self._apply_filters()

"""
Entry point for the Magazine Library application.
"""

import sys
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen
from ui.main_window import MainWindow


def _make_splash() -> QSplashScreen:
    pixmap = QPixmap(420, 160)
    pixmap.fill(QColor("#2b2b2b"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    title_font = QFont("Segoe UI", 18, QFont.Weight.Bold)
    painter.setFont(title_font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pixmap.rect().adjusted(0, -30, 0, 0),
                     Qt.AlignmentFlag.AlignCenter, "Magazine Library")

    sub_font = QFont("Segoe UI", 10)
    painter.setFont(sub_font)
    painter.setPen(QColor("#aaaaaa"))
    painter.drawText(pixmap.rect().adjusted(0, 40, 0, 0),
                     Qt.AlignmentFlag.AlignCenter, "Loading…")

    painter.end()

    splash = QSplashScreen(pixmap)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    return splash


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Magazine Library")
    app.setStyle("Fusion")

    splash = _make_splash()
    splash.show()
    app.processEvents()   # paint the splash before the heavy init begins

    window = MainWindow()
    splash.finish(window)  # dismiss splash the moment the main window is ready
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

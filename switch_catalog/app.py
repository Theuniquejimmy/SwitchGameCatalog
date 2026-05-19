from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .db import connect, init_db
from .settings import load_settings
from .theme import DRACULA_STYLESHEET
from .ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Switch Game Catalog")
    icon_path = Path("E:/Downloads/switch game catalog.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(DRACULA_STYLESHEET)
    conn = connect()
    init_db(conn)
    window = MainWindow(conn, load_settings())
    window.show()
    return app.exec()

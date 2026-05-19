from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .db import connect, init_db
from .paths import BUNDLED_ICON_PATH
from .settings import load_settings
from .theme import DRACULA_STYLESHEET
from .ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Switch Game Catalog")
    app.setWindowIcon(QIcon(str(BUNDLED_ICON_PATH)))
    app.setStyleSheet(DRACULA_STYLESHEET)
    conn = connect()
    init_db(conn)
    window = MainWindow(conn, load_settings())
    window.show()
    return app.exec()

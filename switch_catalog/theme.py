DRACULA_STYLESHEET = """
QWidget {
    background: #282a36;
    color: #f8f8f2;
    font-size: 13px;
}
QMainWindow, QDialog, QMessageBox {
    background: #282a36;
}
QTabWidget::pane {
    border: 1px solid #44475a;
}
QTabBar::tab {
    background: #21222c;
    color: #f8f8f2;
    border: 1px solid #44475a;
    padding: 8px 14px;
    min-width: 96px;
}
QTabBar::tab:selected {
    background: #0078ff;
    color: #f8f8f2;
}
QLineEdit, QTextEdit, QComboBox, QListWidget {
    background: #21222c;
    border: 1px solid #44475a;
    border-radius: 6px;
    color: #f8f8f2;
    selection-background-color: #6272a4;
    selection-color: #f8f8f2;
}
QLineEdit, QComboBox {
    padding: 7px;
}
QTextEdit {
    padding: 8px;
}
QListWidget::item {
    border-radius: 4px;
    padding: 6px;
}
QListWidget::item:selected {
    background: #6272a4;
    color: #f8f8f2;
}
QListWidget::item:selected:!active {
    background: #6272a4;
    color: #f8f8f2;
}
QListWidget::item:hover {
    background: #44475a;
    color: #f8f8f2;
}
QPushButton {
    background: #0078ff;
    border: 0;
    border-radius: 6px;
    color: #f8f8f2;
    font-weight: 700;
    padding: 8px 12px;
}
QPushButton:hover {
    background: #3394ff;
}
QPushButton:pressed {
    background: #005ec7;
}
QPushButton#installButton {
    background: #ff5555;
    color: #f8f8f2;
}
QPushButton#installButton:hover {
    background: #ff6e6e;
}
QPushButton#installButton:pressed {
    background: #d63f3f;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QCheckBox::indicator:unchecked {
    background: #21222c;
    border: 1px solid #6272a4;
    border-radius: 4px;
}
QCheckBox::indicator:checked {
    background: #50fa7b;
    border: 1px solid #50fa7b;
    border-radius: 4px;
}
QSplitter::handle {
    background: #44475a;
}
QProgressBar {
    background: #21222c;
    border: 1px solid #44475a;
    border-radius: 6px;
    color: #f8f8f2;
    text-align: center;
}
QProgressBar::chunk {
    background: #50fa7b;
    border-radius: 6px;
}
QMenu {
    background: #21222c;
    color: #f8f8f2;
    border: 1px solid #6272a4;
    padding: 4px;
}
QMenu::item {
    padding: 8px 24px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #0078ff;
    color: #f8f8f2;
}
QMenu::item:disabled {
    color: #6272a4;
}
"""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QUrl
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .db import row_to_dict
from .file_ops import delete_file_if_present, move_file_to_folder
from .filename import detect_version
from .metadata import apply_metadata_result, fetch_and_apply_metadata, provider_from_settings
from .paths import BUNDLED_ICON_PATH
from .scanner import scan_library
from .settings import AppSettings, normalize_folder, save_settings
from .versions import load_versions, update_status


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection, settings: AppSettings) -> None:
        super().__init__()
        self.conn = conn
        self.settings = settings
        self.network = QNetworkAccessManager(self)
        self.current_game_id: int | None = None
        self.pixmap_cache: dict[str, QPixmap] = {}
        self.context_highlighted_item: QListWidgetItem | None = None
        self.versions = load_versions()

        self.setWindowTitle("Switch Game Catalog")
        self.setWindowIcon(QIcon(str(BUNDLED_ICON_PATH)))
        self.resize(1180, 760)
        self._build_ui()
        self.refresh_games()
        if self.settings.auto_rescan_on_startup and self.settings.base_games_folder:
            self.scan()

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._library_tab(), "Library")
        self.tabs.addTab(self._grid_tab(), "Grid View")
        self.tabs.addTab(self._unmatched_tab(), "Unmatched Updates")
        self.setCentralWidget(self.tabs)

    def _library_tab(self) -> QWidget:
        root = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search library")
        self.search.textChanged.connect(self.refresh_games)
        self.genre_filter = QComboBox()
        self.genre_filter.currentTextChanged.connect(self.refresh_games)
        self.missing_only = QCheckBox("Needs review")
        self.missing_only.stateChanged.connect(self.refresh_games)
        toolbar.addWidget(self.search, 3)
        toolbar.addWidget(self.genre_filter, 1)
        toolbar.addWidget(self.missing_only)

        buttons = QHBoxLayout()
        scan_btn = QPushButton("Rescan")
        scan_btn.clicked.connect(self.scan)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings)
        refresh_metadata_btn = QPushButton("Refresh Metadata")
        refresh_metadata_btn.clicked.connect(self.refresh_metadata)
        refresh_all_metadata_btn = QPushButton("Scan All Metadata")
        refresh_all_metadata_btn.clicked.connect(self.refresh_all_metadata)
        buttons.addWidget(scan_btn)
        buttons.addWidget(refresh_metadata_btn)
        buttons.addWidget(refresh_all_metadata_btn)
        buttons.addWidget(settings_btn)

        self.games_list = QListWidget()
        self.games_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.games_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.games_list.currentItemChanged.connect(self.select_game)
        self.games_list.customContextMenuRequested.connect(self.open_game_menu)
        left_layout.addLayout(toolbar)
        left_layout.addWidget(self.games_list, 1)
        left_layout.addLayout(buttons)
        self.refresh_genres()

        right = QWidget()
        details = QVBoxLayout(right)
        top = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(QSize(275, 375))
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("border: 1px solid #444; background: #151515;")
        info = QVBoxLayout()
        self.title = QLabel("Select a game")
        self.title.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.meta = QLabel("")
        self.meta.setWordWrap(True)
        self.path = QLabel("")
        self.path.setWordWrap(True)
        self.version_status = QLabel("")
        self.version_status.setWordWrap(True)
        self.description = QTextEdit()
        self.description.setReadOnly(True)
        self.description.setPlaceholderText("No description cached yet.")
        info.addWidget(self.title)
        info.addWidget(self.meta)
        info.addWidget(self.path)
        info.addWidget(self.version_status)
        info.addWidget(self.description, 1)
        top.addWidget(self.cover)
        top.addLayout(info, 1)
        details.addLayout(top, 2)

        self.updates = QListWidget()
        self.updates.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.updates.setContextMenuPolicy(Qt.CustomContextMenu)
        self.updates.customContextMenuRequested.connect(self.open_update_menu)
        self.updates.setMaximumHeight(150)
        install_btn = QPushButton("Install Game + Selected Updates")
        install_btn.setObjectName("installButton")
        install_btn.clicked.connect(self.install_selected_game)
        self.screenshots = QListWidget()
        self.screenshots.setViewMode(QListWidget.IconMode)
        self.screenshots.setIconSize(QSize(260, 146))
        self.screenshots.setResizeMode(QListWidget.Adjust)
        self.screenshots.itemClicked.connect(self.open_screenshot)
        details.addWidget(QLabel("DLC/Updates"))
        details.addWidget(self.updates, 0)
        self.newer_updates_label = QLabel("Newer Updates Available")
        self.newer_updates = QListWidget()
        self.newer_updates.setMaximumHeight(110)
        details.addWidget(self.newer_updates_label)
        details.addWidget(self.newer_updates, 0)
        details.addWidget(install_btn)
        details.addWidget(QLabel("Screenshots"))
        details.addWidget(self.screenshots, 3)

        root.addWidget(left)
        root.addWidget(right)
        root.setSizes([370, 810])
        return root

    def _grid_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Art size"))
        self.grid_size = QSlider(Qt.Horizontal)
        self.grid_size.setRange(110, 260)
        self.grid_size.setValue(170)
        self.grid_size.valueChanged.connect(self.update_grid_item_size)
        controls.addWidget(self.grid_size, 1)
        self.grid_size_label = QLabel("")
        controls.addWidget(self.grid_size_label)

        self.grid_list = QListWidget()
        self.grid_list.setViewMode(QListWidget.IconMode)
        self.grid_list.setResizeMode(QListWidget.Adjust)
        self.grid_list.setMovement(QListWidget.Static)
        self.grid_list.setWordWrap(True)
        self.grid_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid_list.itemDoubleClicked.connect(self.open_grid_game)
        self.grid_list.customContextMenuRequested.connect(self.open_grid_menu)

        layout.addWidget(self.grid_list, 1)
        layout.addLayout(controls)
        self.refresh_grid()
        return widget

    def _unmatched_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        controls = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_unmatched)
        self.match_game = QComboBox()
        assign = QPushButton("Assign Selected")
        assign.clicked.connect(self.assign_selected_updates)
        controls.addWidget(refresh)
        controls.addWidget(self.match_game, 1)
        controls.addWidget(assign)
        self.unmatched = QListWidget()
        self.unmatched.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.unmatched.setContextMenuPolicy(Qt.CustomContextMenu)
        self.unmatched.customContextMenuRequested.connect(self.open_unmatched_menu)
        layout.addLayout(controls)
        layout.addWidget(self.unmatched, 1)
        self.refresh_match_games()
        self.refresh_unmatched()
        return widget

    def refresh_games(self) -> None:
        selected = self.current_game_id
        self.clear_context_highlight()
        query = """
            SELECT g.*, gf.file_type, gf.file_path
            FROM games g
            LEFT JOIN game_files gf ON gf.game_id=g.id AND gf.is_base_game=1
            WHERE 1=1
        """
        args: list[object] = []
        search = self.search.text().strip() if hasattr(self, "search") else ""
        if search:
            query += " AND g.display_title LIKE ?"
            args.append(f"{search}%")
        genre = self.genre_filter.currentText() if hasattr(self, "genre_filter") else "All Genres"
        if genre and genre != "All Genres":
            query += " AND g.genres LIKE ?"
            args.append(f'%"{genre}"%')
        if hasattr(self, "missing_only") and self.missing_only.isChecked():
            query += " AND (g.metadata_provider IS NULL OR g.needs_review=1)"
        query += " GROUP BY g.id ORDER BY g.display_title COLLATE NOCASE"

        self.games_list.clear()
        for row in self.conn.execute(query, args):
            text = f"♥ {row['display_title']}" if row["favorite"] else row["display_title"]
            item = QListWidgetItem(text)
            if row["favorite"]:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            item.setData(Qt.UserRole, row["id"])
            self.games_list.addItem(item)
            if selected == row["id"]:
                self.games_list.setCurrentItem(item)

    def refresh_grid(self) -> None:
        if not hasattr(self, "grid_list"):
            return
        self.clear_context_highlight()
        self.grid_list.clear()
        for row in self.conn.execute(
            """
            SELECT id, display_title, cover_image_url, favorite
            FROM games
            ORDER BY display_title COLLATE NOCASE
            """
        ):
            item = QListWidgetItem(row["display_title"])
            item.setData(Qt.UserRole, row["id"])
            cover_url = _higher_res_image_url(row["cover_image_url"] or "")
            item.setData(Qt.UserRole + 1, cover_url)
            item.setData(Qt.UserRole + 2, bool(row["favorite"]))
            self.grid_list.addItem(item)
            if cover_url:
                self._load_list_icon(cover_url, item, self._grid_icon_size(), favorite=bool(row["favorite"]))
        self.update_grid_item_size()

    def update_grid_item_size(self) -> None:
        if not hasattr(self, "grid_list"):
            return
        icon_size = self._grid_icon_size()
        self.grid_list.setIconSize(icon_size)
        self.grid_list.setGridSize(QSize(icon_size.width() + 42, icon_size.height() + 72))
        for index in range(self.grid_list.count()):
            item = self.grid_list.item(index)
            url = item.data(Qt.UserRole + 1)
            if url:
                self._load_list_icon(str(url), item, icon_size, favorite=bool(item.data(Qt.UserRole + 2)))
        if hasattr(self, "grid_size_label"):
            self.grid_size_label.setText(f"{icon_size.width()} px")

    def _grid_icon_size(self) -> QSize:
        width = self.grid_size.value() if hasattr(self, "grid_size") else 170
        return QSize(width, int(width * 1.45))

    def open_grid_game(self, item: QListWidgetItem) -> None:
        game_id = int(item.data(Qt.UserRole))
        self.show_game_in_library(game_id)

    def open_grid_menu(self, position) -> None:
        item = self.grid_list.itemAt(position)
        if item is None:
            return
        self.grid_list.setFocus()
        self.grid_list.setCurrentItem(item)
        item.setSelected(True)
        self.mark_context_item(item)
        QApplication.processEvents()
        game_id = int(item.data(Qt.UserRole))
        favorite = bool(item.data(Qt.UserRole + 2))
        menu = QMenu(self)
        open_action = menu.addAction("Open in library")
        favorite_action = menu.addAction("Remove favorite" if favorite else "Favorite game")
        mark_dlc_action = menu.addAction("Mark as DLC/update")
        chosen = menu.exec(self.grid_list.mapToGlobal(position))
        if chosen == open_action:
            self.show_game_in_library(game_id)
        elif chosen == favorite_action:
            self.toggle_favorite(game_id)
        elif chosen == mark_dlc_action:
            self.mark_game_as_update(game_id)

    def show_game_in_library(self, game_id: int) -> None:
        self.current_game_id = game_id
        if hasattr(self, "search"):
            self.search.clear()
        if hasattr(self, "genre_filter"):
            index = self.genre_filter.findText("All Genres")
            if index >= 0:
                self.genre_filter.setCurrentIndex(index)
        if hasattr(self, "missing_only"):
            self.missing_only.setChecked(False)
        self.refresh_games()
        for index in range(self.games_list.count()):
            item = self.games_list.item(index)
            if int(item.data(Qt.UserRole)) == game_id:
                self.games_list.setCurrentItem(item)
                break
        self.load_game(game_id)
        if hasattr(self, "tabs"):
            self.tabs.setCurrentIndex(0)

    def refresh_genres(self) -> None:
        if not hasattr(self, "genre_filter"):
            return
        current = self.genre_filter.currentText()
        genres = set()
        for row in self.conn.execute("SELECT genres FROM games WHERE genres IS NOT NULL AND genres != ''"):
            try:
                values = json.loads(row["genres"])
            except (TypeError, json.JSONDecodeError):
                continue
            for value in values:
                if isinstance(value, str) and value.strip():
                    genres.add(value.strip())
        self.genre_filter.blockSignals(True)
        self.genre_filter.clear()
        self.genre_filter.addItem("All Genres")
        for genre in sorted(genres, key=str.casefold):
            self.genre_filter.addItem(genre)
        index = self.genre_filter.findText(current)
        if index >= 0:
            self.genre_filter.setCurrentIndex(index)
        self.genre_filter.blockSignals(False)

    def open_game_menu(self, position) -> None:
        item = self.games_list.itemAt(position)
        if item is None:
            return
        self.games_list.setCurrentItem(item)
        item.setSelected(True)
        self.games_list.setFocus()
        self.mark_context_item(item)
        QApplication.processEvents()
        menu = QMenu(self)
        search_action = menu.addAction("Search/change metadata match")
        refresh_action = menu.addAction("Refresh best metadata match")
        favorite = self.is_favorite(int(item.data(Qt.UserRole)))
        favorite_action = menu.addAction("Remove favorite" if favorite else "Favorite game")
        mark_dlc_action = menu.addAction("Mark as DLC/update")
        delete_action = menu.addAction("Delete game file from disk")
        chosen = menu.exec(self.games_list.mapToGlobal(position))
        if chosen == search_action:
            self.search_metadata_match(int(item.data(Qt.UserRole)))
        elif chosen == refresh_action:
            self.refresh_metadata()
        elif chosen == favorite_action:
            self.toggle_favorite(int(item.data(Qt.UserRole)))
        elif chosen == mark_dlc_action:
            self.mark_game_as_update(int(item.data(Qt.UserRole)))
        elif chosen == delete_action:
            self.delete_game(int(item.data(Qt.UserRole)))

    def search_metadata_match(self, game_id: int) -> None:
        if not _metadata_ready(self.settings):
            QMessageBox.information(self, "Metadata", "Add API credentials for the selected provider in Settings.")
            return
        row = self.conn.execute("SELECT display_title, cleaned_title FROM games WHERE id=?", (game_id,)).fetchone()
        if not row:
            return
        dialog = MetadataSearchDialog(
            self.conn,
            self.settings,
            row["cleaned_title"] or row["display_title"],
            self,
        )
        if dialog.exec() == QDialog.Accepted and dialog.selected_result is not None:
            apply_metadata_result(self.conn, game_id, dialog.selected_result, needs_review=False, lock=True)
            self.current_game_id = game_id
            self.refresh_genres()
            self.refresh_games()
            self.refresh_grid()
            self.refresh_match_games()
            self.load_game(game_id)

    def select_game(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        self.current_game_id = int(item.data(Qt.UserRole))
        self.load_game(self.current_game_id)

    def load_game(self, game_id: int) -> None:
        row = self.conn.execute(
            """
            SELECT g.*, gf.file_path, gf.file_size, gf.file_type, gf.modified_time
            FROM games g
            LEFT JOIN game_files gf ON gf.game_id=g.id AND gf.is_base_game=1
            WHERE g.id=?
            """,
            (game_id,),
        ).fetchone()
        game = row_to_dict(row)
        if not game:
            return
        self.title.setText(game["display_title"])
        genres = ", ".join(game.get("genres") or [])
        self.meta.setText(
            f"Release: {game.get('release_date') or 'Unknown'} | "
            f"Developer: {game.get('developer') or 'Unknown'} | "
            f"Publisher: {game.get('publisher') or 'Unknown'} | "
            f"Genres: {genres or 'Unknown'}"
        )
        size = _format_bytes(game.get("file_size") or 0)
        self.path.setText(f"{game.get('file_type') or ''} | {size} | {game.get('file_path') or ''}")
        self.description.setPlainText(game.get("description") or "")
        self.cover.setText("No cover")
        self.cover.setPixmap(QPixmap())
        if game.get("cover_image_url"):
            self._load_image(_higher_res_image_url(game["cover_image_url"]), self.cover, QSize(275, 375))

        self.updates.clear()
        update_rows = self.conn.execute(
            "SELECT id, file_path, file_name, detected_version, match_confidence FROM updates WHERE game_id=? ORDER BY file_name",
            (game_id,),
        ).fetchall()
        for update in update_rows:
            version = f" v{update['detected_version']}" if update["detected_version"] else ""
            confidence = f" ({update['match_confidence']:.0%})"
            item = QListWidgetItem(f"{update['file_name']}{version}{confidence}")
            item.setData(Qt.UserRole, update["id"])
            self.updates.addItem(item)
        status, newer_versions = update_status(
            Path(game.get("file_path") or "").name,
            [row["file_name"] for row in update_rows],
            self.versions,
        )
        self.version_status.setText(status)
        self.newer_updates.clear()
        for version in newer_versions:
            release_date = version.release_date or "release date unavailable"
            self.newer_updates.addItem(f"v{version.version} - {release_date}")
        self.newer_updates_label.setVisible(bool(newer_versions))
        self.newer_updates.setVisible(bool(newer_versions))

        self.screenshots.clear()
        for shot in self.conn.execute(
            "SELECT image_url FROM screenshots WHERE game_id=? ORDER BY sort_order LIMIT 8",
            (game_id,),
        ):
            item = QListWidgetItem("Loading")
            item.setData(Qt.UserRole, _higher_res_image_url(shot["image_url"]))
            item.setSizeHint(QSize(290, 180))
            self.screenshots.addItem(item)
            self._load_list_icon(_higher_res_image_url(shot["image_url"]), item, QSize(260, 146), clear_text=True)

    def _load_image(self, url: str, label: QLabel, size: QSize) -> None:
        reply = self.network.get(QNetworkRequest(QUrl(url)))

        def done() -> None:
            data = reply.readAll()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                label.setPixmap(pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                label.setText("")
            reply.deleteLater()

        reply.finished.connect(done)

    def _load_list_icon(
        self,
        url: str,
        item: QListWidgetItem,
        size: QSize,
        *,
        favorite: bool = False,
        clear_text: bool = False,
    ) -> None:
        if url in self.pixmap_cache:
            try:
                self._set_item_icon(item, self.pixmap_cache[url], size, favorite=favorite)
            except RuntimeError:
                pass
            return
        reply = self.network.get(QNetworkRequest(QUrl(url)))

        def done() -> None:
            data = reply.readAll()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.pixmap_cache[url] = pixmap
                try:
                    self._set_item_icon(item, pixmap, size, favorite=favorite)
                    if clear_text:
                        item.setText("")
                except RuntimeError:
                    pass
            else:
                try:
                    item.setText("Image unavailable")
                except RuntimeError:
                    pass
            reply.deleteLater()

        reply.finished.connect(done)

    def _set_item_icon(self, item: QListWidgetItem, pixmap: QPixmap, size: QSize, *, favorite: bool = False) -> None:
        scaled = pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if favorite:
            scaled = _favorite_pixmap(scaled)
        item.setIcon(QIcon(scaled))

    def open_screenshot(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.UserRole)
        if not url:
            return
        urls = [self.screenshots.item(index).data(Qt.UserRole) for index in range(self.screenshots.count())]
        current_index = self.screenshots.row(item)
        dialog = ImagePreviewDialog([str(value) for value in urls if value], current_index, self.network, self)
        dialog.exec()

    def open_update_menu(self, position) -> None:
        item = self.updates.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.updates.clearSelection()
            item.setSelected(True)
        self.updates.setCurrentItem(item)
        self.updates.setFocus()
        self.mark_context_item(item)
        QApplication.processEvents()
        menu = QMenu(self)
        install_action = menu.addAction("Install selected update/DLC file(s)")
        delete_action = menu.addAction("Delete selected update file(s)")
        unmatch_action = menu.addAction("Unmatch selected update(s)")
        chosen = menu.exec(self.updates.mapToGlobal(position))
        if chosen == install_action:
            self.install_selected_updates_only()
        elif chosen == delete_action:
            self.delete_selected_updates()
        elif chosen == unmatch_action:
            self.unmatch_selected_updates()

    def selected_update_ids(self) -> list[int]:
        return [int(item.data(Qt.UserRole)) for item in self.updates.selectedItems()]

    def selected_unmatched_update_ids(self) -> list[int]:
        return [int(item.data(Qt.UserRole)) for item in self.unmatched.selectedItems()]

    def install_selected_game(self) -> None:
        if self.current_game_id is None:
            return
        if not self.settings.install_folder:
            self.open_settings()
            if not self.settings.install_folder:
                return
        base_row = self.conn.execute(
            "SELECT id, file_path, file_name FROM game_files WHERE game_id=? AND is_base_game=1 ORDER BY id LIMIT 1",
            (self.current_game_id,),
        ).fetchone()
        if not base_row:
            QMessageBox.warning(self, "Install", "No base game file is recorded for this game.")
            return
        update_rows = []
        update_ids = self.selected_update_ids()
        if update_ids:
            placeholders = ",".join("?" for _ in update_ids)
            update_rows = self.conn.execute(
                f"SELECT id, file_path, file_name FROM updates WHERE id IN ({placeholders}) ORDER BY file_name",
                update_ids,
            ).fetchall()
        message = (
            f"Move the base game first, then {len(update_rows)} selected update/DLC file(s), into:\n"
            f"{self.settings.install_folder}"
        )
        if QMessageBox.question(self, "Install files", message) != QMessageBox.Yes:
            return
        moved = 0
        try:
            base_destination = move_file_to_folder(base_row["file_path"], self.settings.install_folder)
            self.conn.execute(
                """
                UPDATE game_files
                SET file_path=?, file_name=?, file_extension=?, modified_time=?
                WHERE id=?
                """,
                (
                    str(base_destination),
                    base_destination.name,
                    base_destination.suffix.lower(),
                    base_destination.stat().st_mtime,
                    int(base_row["id"]),
                ),
            )
            moved += 1
            for update in update_rows:
                destination = move_file_to_folder(update["file_path"], self.settings.install_folder)
                self.conn.execute(
                    """
                    UPDATE updates
                    SET file_path=?, file_name=?, modified_time=?
                    WHERE id=?
                    """,
                    (str(destination), destination.name, destination.stat().st_mtime, int(update["id"])),
                )
                moved += 1
            self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            QMessageBox.warning(self, "Install failed", str(exc))
            return
        self.load_game(self.current_game_id)
        self.refresh_games()
        QMessageBox.information(self, "Install complete", f"Moved {moved} file(s).")

    def install_selected_updates_only(self) -> None:
        if not self.settings.install_folder:
            self.open_settings()
            if not self.settings.install_folder:
                return
        update_ids = self.selected_update_ids()
        if not update_ids and hasattr(self, "unmatched"):
            update_ids = self.selected_unmatched_update_ids()
        if not update_ids:
            return
        placeholders = ",".join("?" for _ in update_ids)
        rows = self.conn.execute(
            f"SELECT id, file_path, file_name FROM updates WHERE id IN ({placeholders}) ORDER BY file_name",
            update_ids,
        ).fetchall()
        if QMessageBox.question(
            self,
            "Install update/DLC files",
            f"Move {len(rows)} selected update/DLC file(s) into:\n{self.settings.install_folder}",
        ) != QMessageBox.Yes:
            return
        moved = 0
        try:
            for row in rows:
                destination = move_file_to_folder(row["file_path"], self.settings.install_folder)
                self.conn.execute(
                    """
                    UPDATE updates
                    SET file_path=?, file_name=?, modified_time=?
                    WHERE id=?
                    """,
                    (str(destination), destination.name, destination.stat().st_mtime, int(row["id"])),
                )
                moved += 1
            self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            QMessageBox.warning(self, "Install failed", str(exc))
            return
        if self.current_game_id:
            self.load_game(self.current_game_id)
        self.refresh_unmatched()
        QMessageBox.information(self, "Install complete", f"Moved {moved} update/DLC file(s).")

    def delete_selected_updates(self) -> None:
        update_ids = self.selected_update_ids()
        if not update_ids:
            return
        self.delete_updates_by_ids(update_ids)

    def delete_updates_by_ids(self, update_ids: list[int]) -> None:
        placeholders = ",".join("?" for _ in update_ids)
        rows = self.conn.execute(
            f"SELECT id, file_path, file_name FROM updates WHERE id IN ({placeholders})",
            update_ids,
        ).fetchall()
        if QMessageBox.question(
            self,
            "Delete update files",
            f"Delete {len(rows)} selected update/DLC file(s) from disk and remove them from the catalog?",
        ) != QMessageBox.Yes:
            return
        deleted = 0
        try:
            for row in rows:
                if delete_file_if_present(row["file_path"]):
                    deleted += 1
                self.conn.execute("DELETE FROM updates WHERE id=?", (int(row["id"]),))
            self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        if self.current_game_id:
            self.load_game(self.current_game_id)
        self.clear_context_highlight()
        self.refresh_unmatched()
        self.refresh_games()
        self.refresh_grid()
        QMessageBox.information(self, "Delete complete", f"Deleted {deleted} file(s).")

    def unmatch_selected_updates(self) -> None:
        update_ids = self.selected_update_ids()
        if not update_ids:
            return
        self.conn.executemany(
            "UPDATE updates SET game_id=NULL, match_confidence=0, manual_match=0 WHERE id=?",
            [(update_id,) for update_id in update_ids],
        )
        self.conn.commit()
        if self.current_game_id:
            self.load_game(self.current_game_id)
        self.refresh_unmatched()
        self.refresh_games()

    def delete_game(self, game_id: int) -> None:
        row = self.conn.execute("SELECT display_title FROM games WHERE id=?", (game_id,)).fetchone()
        file_rows = self.conn.execute(
            "SELECT id, file_path FROM game_files WHERE game_id=? AND is_base_game=1",
            (game_id,),
        ).fetchall()
        if not row or not file_rows:
            return
        if QMessageBox.question(
            self,
            "Delete game file",
            f"Delete {row['display_title']} from disk and remove it from the catalog?",
        ) != QMessageBox.Yes:
            return
        deleted = 0
        try:
            for file_row in file_rows:
                if delete_file_if_present(file_row["file_path"]):
                    deleted += 1
            self.conn.execute("DELETE FROM games WHERE id=?", (game_id,))
            self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        self.current_game_id = None
        self.clear_context_highlight()
        self.refresh_games()
        self.refresh_grid()
        self.refresh_match_games()
        self.refresh_unmatched()
        self.title.setText("Select a game")
        self.meta.setText("")
        self.path.setText("")
        self.description.clear()
        self.cover.clear()
        self.updates.clear()
        self.screenshots.clear()
        QMessageBox.information(self, "Delete complete", f"Deleted {deleted} game file(s).")

    def is_favorite(self, game_id: int) -> bool:
        row = self.conn.execute("SELECT favorite FROM games WHERE id=?", (game_id,)).fetchone()
        return bool(row and row["favorite"])

    def toggle_favorite(self, game_id: int) -> None:
        self.conn.execute("UPDATE games SET favorite=CASE WHEN favorite=1 THEN 0 ELSE 1 END WHERE id=?", (game_id,))
        self.conn.commit()
        self.refresh_games()
        self.refresh_grid()
        if self.current_game_id == game_id:
            self.load_game(game_id)

    def mark_game_as_update(self, game_id: int) -> None:
        row = self.conn.execute(
            """
            SELECT g.display_title, gf.file_path, gf.file_name, gf.file_size, gf.modified_time
            FROM games g
            JOIN game_files gf ON gf.game_id=g.id AND gf.is_base_game=1
            WHERE g.id=?
            ORDER BY gf.id LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        if not row:
            return
        if QMessageBox.question(
            self,
            "Mark as DLC/update",
            f"Move {row['display_title']} out of the game list and into Unmatched Updates?",
        ) != QMessageBox.Yes:
            return
        self.conn.execute(
            """
            INSERT INTO updates(game_id, file_path, file_name, detected_version, file_size, modified_time, match_confidence, manual_match)
            VALUES (NULL, ?, ?, ?, ?, ?, 0, 0)
            ON CONFLICT(file_path) DO UPDATE SET
                game_id=NULL,
                file_name=excluded.file_name,
                detected_version=excluded.detected_version,
                file_size=excluded.file_size,
                modified_time=excluded.modified_time,
                match_confidence=0,
                manual_match=0
            """,
            (
                row["file_path"],
                row["file_name"],
                detect_version(row["file_name"]),
                row["file_size"],
                row["modified_time"],
            ),
        )
        self.conn.execute("DELETE FROM games WHERE id=?", (game_id,))
        self.conn.commit()
        if self.current_game_id == game_id:
            self.current_game_id = None
            self.title.setText("Select a game")
            self.meta.setText("")
            self.path.setText("")
            self.description.clear()
            self.cover.clear()
            self.updates.clear()
            self.screenshots.clear()
        self.refresh_games()
        self.refresh_grid()
        self.refresh_match_games()
        self.refresh_unmatched()
        if hasattr(self, "tabs"):
            self.tabs.setCurrentIndex(2)

    def mark_context_item(self, item: QListWidgetItem) -> None:
        self.clear_context_highlight(except_item=item)
        item.setBackground(QBrush(QColor("#ff79c6")))
        self.context_highlighted_item = item

    def clear_context_highlight(self, except_item: QListWidgetItem | None = None) -> None:
        if self.context_highlighted_item is None or self.context_highlighted_item is except_item:
            return
        try:
            self.context_highlighted_item.setBackground(QBrush())
            self.context_highlighted_item.setForeground(QBrush(QColor("#f8f8f2")))
        except RuntimeError:
            pass
        self.context_highlighted_item = None

    def scan(self) -> None:
        if not self.settings.base_games_folder:
            self.open_settings()
            if not self.settings.base_games_folder:
                return
        summary = scan_library(
            self.conn,
            self.settings.base_games_folder,
            self.settings.updates_folder,
            recursive=self.settings.scan_recursively,
            threshold=self.settings.fuzzy_match_threshold,
        )
        self.versions = load_versions(refresh=True)
        self.refresh_games()
        self.refresh_grid()
        self.refresh_unmatched()
        self.refresh_match_games()
        if self.current_game_id:
            self.load_game(self.current_game_id)
        QMessageBox.information(
            self,
            "Scan complete",
            f"Base files: {summary.base_files}\nUpdates: {summary.update_files}\n"
            f"Matched updates: {summary.matched_updates}\nUnmatched updates: {summary.unmatched_updates}",
        )

    def refresh_metadata(self) -> None:
        if self.current_game_id is None:
            return
        row = self.conn.execute(
            "SELECT display_title, cleaned_title FROM games WHERE id=?",
            (self.current_game_id,),
        ).fetchone()
        if not row:
            return
        try:
            ok = fetch_and_apply_metadata(
                self.conn,
                self.current_game_id,
                row["cleaned_title"] or row["display_title"],
                provider=self.settings.metadata_provider,
                igdb_client_id=self.settings.igdb_client_id,
                igdb_client_secret=self.settings.igdb_client_secret,
                force=True,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Metadata error", str(exc))
            return
        if not ok:
            QMessageBox.information(self, "Metadata", "No metadata result found. Check provider settings/API key.")
        self.refresh_genres()
        self.load_game(self.current_game_id)
        self.refresh_games()
        self.refresh_grid()

    def refresh_all_metadata(self) -> None:
        if not _metadata_ready(self.settings):
            QMessageBox.information(self, "Metadata", "Add API credentials for the selected provider in Settings.")
            return
        rows = self.conn.execute(
            """
            SELECT id, display_title, cleaned_title
            FROM games
            WHERE metadata_locked=0
              AND (
                metadata_provider IS NULL
                OR metadata_provider != ?
                OR description IS NULL
                OR description=''
                OR cover_image_url IS NULL
                OR cover_image_url=''
                OR needs_review=1
              )
            ORDER BY display_title COLLATE NOCASE
            """,
            (self.settings.metadata_provider,),
        ).fetchall()
        if not rows:
            QMessageBox.information(self, "Metadata", "All unlocked games already have cached metadata.")
            return
        progress = QProgressDialog("Scanning metadata...", "Cancel", 0, len(rows), self)
        progress.setWindowTitle("Metadata Scan")
        progress.setWindowModality(Qt.WindowModal)
        updated = 0
        no_match = 0
        failures = 0
        for index, row in enumerate(rows, start=1):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"Scanning {index} of {len(rows)}: {row['display_title']}")
            progress.setValue(index - 1)
            QApplication.processEvents()
            try:
                if fetch_and_apply_metadata(
                    self.conn,
                    int(row["id"]),
                    row["cleaned_title"] or row["display_title"],
                    provider=self.settings.metadata_provider,
                    igdb_client_id=self.settings.igdb_client_id,
                    igdb_client_secret=self.settings.igdb_client_secret,
                ):
                    updated += 1
                else:
                    no_match += 1
            except Exception as exc:
                self.conn.execute("UPDATE games SET needs_review=1 WHERE id=?", (int(row["id"]),))
                self.conn.commit()
                failures += 1
        progress.setValue(len(rows))
        self.refresh_genres()
        self.refresh_games()
        self.refresh_grid()
        if self.current_game_id:
            self.load_game(self.current_game_id)
        message = f"Scanned {len(rows)} games.\nUpdated: {updated}\nNo match: {no_match}"
        if failures:
            message += f"\nFailed: {failures}"
        if no_match or failures:
            message += "\nThose games were marked for review."
        QMessageBox.information(self, "Metadata scan complete", message)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            self.settings = dialog.settings
            save_settings(self.settings)
            self.refresh_match_games()

    def refresh_unmatched(self) -> None:
        if not hasattr(self, "unmatched"):
            return
        self.clear_context_highlight()
        self.unmatched.clear()
        for row in self.conn.execute(
            "SELECT id, file_name, file_path, detected_version FROM updates WHERE game_id IS NULL ORDER BY file_name"
        ):
            version = f" | v{row['detected_version']}" if row["detected_version"] else ""
            item = QListWidgetItem(f"{row['file_name']}{version}\n{row['file_path']}")
            item.setData(Qt.UserRole, row["id"])
            self.unmatched.addItem(item)

    def open_unmatched_menu(self, position) -> None:
        item = self.unmatched.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.unmatched.clearSelection()
            item.setSelected(True)
        self.unmatched.setCurrentItem(item)
        self.unmatched.setFocus()
        self.mark_context_item(item)
        QApplication.processEvents()
        menu = QMenu(self)
        install_action = menu.addAction("Install selected update/DLC file(s)")
        assign_action = menu.addAction("Assign selected to chosen game")
        delete_action = menu.addAction("Delete selected update/DLC file(s)")
        chosen = menu.exec(self.unmatched.mapToGlobal(position))
        if chosen == install_action:
            self.install_selected_updates_only()
        elif chosen == assign_action:
            self.assign_selected_updates()
        elif chosen == delete_action:
            self.delete_unmatched_updates()

    def delete_unmatched_updates(self) -> None:
        update_ids = self.selected_unmatched_update_ids()
        if not update_ids:
            return
        self.delete_updates_by_ids(update_ids)

    def refresh_match_games(self) -> None:
        if not hasattr(self, "match_game"):
            return
        current = self.match_game.currentData()
        self.match_game.clear()
        for row in self.conn.execute("SELECT id, display_title FROM games ORDER BY display_title COLLATE NOCASE"):
            self.match_game.addItem(row["display_title"], row["id"])
        if current is not None:
            index = self.match_game.findData(current)
            if index >= 0:
                self.match_game.setCurrentIndex(index)

    def assign_selected_updates(self) -> None:
        game_id = self.match_game.currentData() if hasattr(self, "match_game") else None
        selected = self.unmatched.selectedItems() if hasattr(self, "unmatched") else []
        if not game_id or not selected:
            QMessageBox.information(self, "Assign updates", "Select one or more updates and a target game.")
            return
        update_ids = [int(item.data(Qt.UserRole)) for item in selected]
        self.conn.executemany(
            "UPDATE updates SET game_id=?, match_confidence=1, manual_match=1 WHERE id=?",
            [(int(game_id), update_id) for update_id in update_ids],
        )
        self.conn.commit()
        self.refresh_unmatched()
        self.refresh_games()
        if self.current_game_id:
            self.load_game(self.current_game_id)
        QMessageBox.information(self, "Assign updates", f"Assigned {len(update_ids)} update file(s).")


class ImagePreviewDialog(QDialog):
    def __init__(self, urls: list[str], current_index: int, network: QNetworkAccessManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Screenshot")
        self.urls = urls
        self.current_index = max(0, min(current_index, len(urls) - 1))
        self.network = network
        self.resize(980, 620)
        layout = QVBoxLayout(self)
        self.image = QLabel("Loading image...")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(QSize(760, 460))
        layout.addWidget(self.image, 1)
        nav = QHBoxLayout()
        self.previous = QPushButton("Previous")
        self.previous.clicked.connect(self.show_previous)
        self.counter = QLabel("")
        self.counter.setAlignment(Qt.AlignCenter)
        self.next = QPushButton("Next")
        self.next.clicked.connect(self.show_next)
        nav.addWidget(self.previous)
        nav.addWidget(self.counter, 1)
        nav.addWidget(self.next)
        layout.addLayout(nav)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.load_current()

    def load_current(self) -> None:
        if not self.urls:
            self.image.setText("No screenshots")
            return
        self.image.setText("Loading image...")
        self.image.setPixmap(QPixmap())
        self.counter.setText(f"{self.current_index + 1} of {len(self.urls)}")
        self.previous.setEnabled(self.current_index > 0)
        self.next.setEnabled(self.current_index < len(self.urls) - 1)
        reply = self.network.get(QNetworkRequest(QUrl(self.urls[self.current_index])))

        def done() -> None:
            pixmap = QPixmap()
            if pixmap.loadFromData(reply.readAll()):
                self.image.setPixmap(
                    pixmap.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                self.image.setText("")
            else:
                self.image.setText("Image unavailable")
            reply.deleteLater()

        reply.finished.connect(done)

    def show_previous(self) -> None:
        if self.current_index > 0:
            self.current_index -= 1
            self.load_current()

    def show_next(self) -> None:
        if self.current_index < len(self.urls) - 1:
            self.current_index += 1
            self.load_current()


class MetadataSearchDialog(QDialog):
    def __init__(self, conn: sqlite3.Connection, settings: AppSettings, title: str, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.settings = settings
        self.selected_result = None
        self.provider = provider_from_settings(
            settings.metadata_provider,
            igdb_client_id=settings.igdb_client_id,
            igdb_client_secret=settings.igdb_client_secret,
        )
        self.setWindowTitle("Search Metadata")
        self.resize(720, 520)

        layout = QVBoxLayout(self)
        search_row = QHBoxLayout()
        self.query = QLineEdit(title)
        search = QPushButton("Search")
        search.clicked.connect(self.search)
        search_row.addWidget(self.query, 1)
        search_row.addWidget(search)

        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self.accept_selected)
        actions = QHBoxLayout()
        choose = QPushButton("Use Selected")
        choose.clicked.connect(self.accept_selected)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(choose)
        actions.addWidget(cancel)

        layout.addLayout(search_row)
        layout.addWidget(self.results, 1)
        layout.addLayout(actions)
        self.search()

    def search(self) -> None:
        self.results.clear()
        query = self.query.text().strip()
        if not query:
            return
        try:
            results = self.provider.search(self.conn, query)
        except Exception as exc:
            QMessageBox.warning(self, "Metadata search failed", str(exc))
            return
        for result in results:
            item = QListWidgetItem(
                f"{result.title}\n"
                f"Released: {result.release_date or 'Unknown'} | Confidence: {result.confidence:.0%}"
            )
            item.setData(Qt.UserRole, result)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def accept_selected(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        result = item.data(Qt.UserRole)
        try:
            self.selected_result = self.provider.enrich(self.conn, result)
        except Exception as exc:
            QMessageBox.warning(self, "Metadata details failed", str(exc))
            return
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        layout = QFormLayout(self)
        self.base = _folder_field(settings.base_games_folder)
        self.updates = _folder_field(settings.updates_folder)
        self.install = _folder_field(settings.install_folder)
        self.provider = QComboBox()
        self.provider.addItems(["igdb"])
        self.provider.setCurrentText(settings.metadata_provider)
        self.igdb_client_id = QLineEdit(settings.igdb_client_id)
        self.igdb_client_secret = QLineEdit(settings.igdb_client_secret)
        self.igdb_client_secret.setEchoMode(QLineEdit.Password)
        self.recursive = QCheckBox()
        self.recursive.setChecked(settings.scan_recursively)
        self.auto = QCheckBox()
        self.auto.setChecked(settings.auto_rescan_on_startup)
        layout.addRow("Base games folder", self.base)
        layout.addRow("Updates folder", self.updates)
        layout.addRow("Install folder", self.install)
        layout.addRow("Metadata provider", self.provider)
        layout.addRow("IGDB client ID", self.igdb_client_id)
        layout.addRow("IGDB client secret", self.igdb_client_secret)
        layout.addRow("Scan recursively", self.recursive)
        layout.addRow("Auto-rescan on startup", self.auto)
        actions = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(save)
        actions.addWidget(cancel)
        layout.addRow(actions)

    def accept(self) -> None:
        self.settings.base_games_folder = normalize_folder(self.base.text())
        self.settings.updates_folder = normalize_folder(self.updates.text())
        self.settings.install_folder = normalize_folder(self.install.text())
        self.settings.metadata_provider = self.provider.currentText()
        self.settings.igdb_client_id = self.igdb_client_id.text().strip()
        self.settings.igdb_client_secret = self.igdb_client_secret.text().strip()
        self.settings.scan_recursively = self.recursive.isChecked()
        self.settings.auto_rescan_on_startup = self.auto.isChecked()
        super().accept()


def _folder_field(value: str) -> QWidget:
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    line = QLineEdit(value)
    browse = QPushButton("Browse")

    def choose() -> None:
        folder = QFileDialog.getExistingDirectory(wrapper, "Choose folder", line.text())
        if folder:
            line.setText(folder)

    browse.clicked.connect(choose)
    layout.addWidget(line, 1)
    layout.addWidget(browse)
    wrapper.text = line.text  # type: ignore[attr-defined]
    return wrapper


def _metadata_ready(settings: AppSettings) -> bool:
    return bool(settings.igdb_client_id and settings.igdb_client_secret)


def _favorite_pixmap(pixmap: QPixmap) -> QPixmap:
    framed = QPixmap(pixmap.size() + QSize(10, 10))
    framed.fill(Qt.transparent)
    painter = QPainter(framed)
    painter.drawPixmap(5, 5, pixmap)
    pen = QPen(QColor("#ff79c6"), 5)
    painter.setPen(pen)
    painter.drawRoundedRect(2, 2, framed.width() - 4, framed.height() - 4, 8, 8)
    painter.end()
    return framed


def _higher_res_image_url(url: str) -> str:
    if "images.igdb.com" not in url:
        return url
    if "t_screenshot_" in url or "t_720p" in url:
        return (
            url.replace("t_screenshot_med", "t_1080p")
            .replace("t_screenshot_big", "t_1080p")
            .replace("t_screenshot_huge", "t_1080p")
            .replace("t_720p", "t_1080p")
        )
    if "t_cover_big_2x" in url:
        return url
    if "t_cover_big" in url:
        return url.replace("t_cover_big", "t_cover_big_2x")
    if "t_thumb" in url:
        return url.replace("t_thumb", "t_cover_big_2x")
    return url


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"

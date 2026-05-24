from __future__ import annotations

import base64
from concurrent.futures import Future, ThreadPoolExecutor
import html
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from . import __version__
from PySide6.QtCore import Qt, QSize, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
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
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:  # pragma: no cover - depends on PySide6 packaging.
    QWebEngineView = None

from .app_updates import RELEASES_PAGE_URL, check_latest_release
from .db import reset_library_cache, row_to_dict
from .file_ops import (
    delete_file_if_present,
    is_shell_path,
    move_file_to_folder,
    move_files_to_folder,
    mtp_destination_storage_info,
    mtp_install_destination_info,
    mtp_install_destination_label,
    mtp_storage_status,
)
from .filename import detect_version, extract_title_id
from .http_server import CatalogHttpServer, switch_directory_url
from .metadata import apply_metadata_result, fetch_and_apply_metadata, provider_from_settings
from .paths import BUNDLED_ICON_PATH
from .scanner import scan_library
from .settings import AppSettings, normalize_folder, save_settings
from .versions import (
    file_version_number,
    load_versions,
    raw_version_to_dotted,
    refresh_versions_if_stale,
    released_version_label,
    update_status,
    version_label,
)


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
        self.catalog_server = CatalogHttpServer()
        self.install_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mtp-install")
        self.active_install_futures: list[Future] = []

        self.setWindowTitle("Switch Game Catalog")
        self.setWindowIcon(QIcon(str(BUNDLED_ICON_PATH)))
        self.resize(1180, 760)
        self._build_ui()
        self.refresh_games()
        QTimer.singleShot(1500, self.refresh_mtp_storage_status)
        if self.settings.auto_rescan_on_startup and self.settings.base_games_folder:
            self.scan()
        if self.settings.auto_check_updates_on_startup:
            QTimer.singleShot(1000, lambda: self.check_for_app_updates(silent=True))
        self._sync_http_server(show_errors=False)

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.tabBar().setExpanding(False)
        self.library_tab_widget = self._library_tab()
        self.grid_tab_widget = self._grid_tab()
        self.favorites_tab_widget = self._favorites_tab()
        self.unmatched_tab_widget = self._unmatched_tab()
        self.tabs.addTab(self.library_tab_widget, "Library")
        self.tabs.addTab(self.grid_tab_widget, "Grid View")
        self.tabs.addTab(self.favorites_tab_widget, "Favorites")
        self.setCentralWidget(self.tabs)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setStyleSheet("QStatusBar::item { border: 0; }")
        self.mtp_storage_label = QLabel("")
        self.mtp_storage_label.setToolTip("Switch MTP storage free space")
        self.mtp_storage_label.setVisible(False)
        self.statusBar().addPermanentWidget(self.mtp_storage_label)
        self.mtp_storage_timer = QTimer(self)
        self.mtp_storage_timer.setInterval(60_000)
        self.mtp_storage_timer.timeout.connect(self.refresh_mtp_storage_status)
        self.mtp_storage_timer.start()
        self.refresh_unmatched()

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
        filters = QVBoxLayout()
        self.missing_only = QCheckBox("Need Review")
        self.missing_only.stateChanged.connect(self.refresh_games)
        self.updates_only = QCheckBox("Needs Update?")
        self.updates_only.stateChanged.connect(self.refresh_games)
        filters.addWidget(self.missing_only)
        filters.addWidget(self.updates_only)
        toolbar.addWidget(self.search, 3)
        toolbar.addWidget(self.genre_filter, 1)
        toolbar.addLayout(filters)

        buttons = QHBoxLayout()
        scan_btn = QPushButton("Rescan Library")
        scan_btn.clicked.connect(self.scan)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings)
        refresh_all_metadata_btn = QPushButton("Scan All Metadata")
        refresh_all_metadata_btn.clicked.connect(self.refresh_all_metadata)
        buttons.addWidget(scan_btn)
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
        self.meta.setTextFormat(Qt.RichText)
        self.meta.setOpenExternalLinks(False)
        self.meta.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.meta.linkActivated.connect(self.open_trailer)
        self.path = QLabel("")
        self.path.setWordWrap(True)
        self.version_status = QLabel("")
        self.version_status.setWordWrap(True)
        self.installed_status = QLabel("")
        self.installed_status.setWordWrap(True)
        self.description = QTextEdit()
        self.description.setReadOnly(True)
        self.description.setPlaceholderText("No description cached yet.")
        info.addWidget(self.title)
        info.addWidget(self.meta)
        info.addWidget(self.path)
        info.addWidget(self.version_status)
        info.addWidget(self.installed_status)
        info.addWidget(self.description, 1)
        top.addWidget(self.cover)
        top.addLayout(info, 1)
        details.addLayout(top, 2)

        self.updates = QListWidget()
        self.updates.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.updates.setContextMenuPolicy(Qt.CustomContextMenu)
        self.updates.customContextMenuRequested.connect(self.open_update_menu)
        self.updates.itemSelectionChanged.connect(self.update_install_estimate)
        self.updates.setMaximumHeight(150)
        install_btn = QPushButton("Install Game + Selected Updates")
        install_btn.setObjectName("installButton")
        install_btn.clicked.connect(self.install_selected_game)
        self.install_estimate = QLabel("Install size: select a game")
        self.install_estimate.setWordWrap(True)
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
        details.addWidget(self.install_estimate)
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
        self._configure_grid_list(self.grid_list)
        self.grid_list.itemDoubleClicked.connect(self.open_grid_game)
        self.grid_list.customContextMenuRequested.connect(self.open_grid_menu)

        layout.addWidget(self.grid_list, 1)
        layout.addLayout(controls)
        self.refresh_grid()
        return widget

    def _favorites_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.favorites_grid_list = QListWidget()
        self._configure_grid_list(self.favorites_grid_list)
        self.favorites_grid_list.itemDoubleClicked.connect(self.open_grid_game)
        self.favorites_grid_list.customContextMenuRequested.connect(self.open_favorites_grid_menu)
        layout.addWidget(self.favorites_grid_list, 1)
        self.refresh_favorites_grid()
        return widget

    def _configure_grid_list(self, grid_list: QListWidget) -> None:
        grid_list.setViewMode(QListWidget.IconMode)
        grid_list.setResizeMode(QListWidget.Adjust)
        grid_list.setMovement(QListWidget.Static)
        grid_list.setWordWrap(True)
        grid_list.setUniformItemSizes(True)
        grid_list.setSpacing(10)
        grid_list.setSelectionMode(QAbstractItemView.SingleSelection)
        grid_list.setContextMenuPolicy(Qt.CustomContextMenu)

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

        updates_only = hasattr(self, "updates_only") and self.updates_only.isChecked()
        if updates_only:
            self.refresh_versions()

        self.games_list.clear()
        for row in self.conn.execute(query, args):
            if updates_only and not self.game_needs_update(int(row["id"]), row["file_path"]):
                continue
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

    def refresh_versions(self) -> None:
        self.versions = refresh_versions_if_stale(self.versions)

    def refresh_mtp_storage_status(self) -> None:
        if not hasattr(self, "mtp_storage_label"):
            return
        status = mtp_storage_status(timeout_seconds=6)
        self.mtp_storage_label.setText(status)
        self.mtp_storage_label.setVisible(bool(status))

    def resolve_install_destination(self, *, show_error: bool = False) -> str:
        destination = getattr(self.settings, "install_destination", "local")
        if destination in {"nand", "sd"}:
            storage = mtp_install_destination_info(destination, timeout_seconds=8)
            if storage is None:
                if show_error:
                    QMessageBox.warning(
                        self,
                        "Switch install destination",
                        f"Could not find {mtp_install_destination_label(destination)}. Connect the Switch over MTP and try again.",
                    )
                return ""
            self.settings.install_folder = normalize_folder(storage.path)
            self.settings.install_folder_label = mtp_install_destination_label(destination)
            return self.settings.install_folder
        return self.settings.install_folder

    def ensure_install_destination_configured(self) -> bool:
        if self.resolve_install_destination(show_error=False):
            return True
        self.open_settings()
        return bool(self.resolve_install_destination(show_error=True))

    def install_destination_space_failure(self, total_size: int) -> str:
        install_folder = self.resolve_install_destination(show_error=False)
        if not install_folder or total_size <= 0:
            return ""
        if is_shell_path(install_folder):
            storage = mtp_destination_storage_info(install_folder, timeout_seconds=8)
            self.refresh_mtp_storage_status()
            if storage is None:
                return ""
            if total_size <= storage.free_bytes:
                return ""
            return (
                f"{storage.name} does not have enough free space for this install.\n\n"
                f"Needed: {_format_bytes(total_size)}\n"
                f"Available: {_format_bytes(storage.free_bytes)}"
            )
        try:
            free_bytes = shutil.disk_usage(str(Path(install_folder))).free
        except OSError:
            return ""
        if total_size <= free_bytes:
            return ""
        return (
            "The install folder does not have enough free space for this install.\n\n"
            f"Needed: {_format_bytes(total_size)}\n"
            f"Available: {_format_bytes(free_bytes)}"
        )

    def ensure_install_destination_has_space(self, total_size: int) -> bool:
        failure = self.install_destination_space_failure(total_size)
        if not failure:
            return True
        QMessageBox.warning(self, "Not enough space", failure)
        return False

    def game_needs_update(self, game_id: int, base_file_path: str | None) -> bool:
        update_rows = self.conn.execute(
            "SELECT file_name FROM updates WHERE game_id=?",
            (game_id,),
        ).fetchall()
        _, newer_versions = update_status(
            Path(base_file_path or "").name,
            [row["file_name"] for row in update_rows],
            self.versions,
        )
        return bool(newer_versions)

    def update_install_estimate(self) -> None:
        if not hasattr(self, "install_estimate"):
            return
        if self.current_game_id is None:
            self.install_estimate.setText("Install size: select a game")
            return
        base = self.conn.execute(
            "SELECT file_size FROM game_files WHERE game_id=? AND is_base_game=1 ORDER BY id LIMIT 1",
            (self.current_game_id,),
        ).fetchone()
        base_size = int(base["file_size"] or 0) if base else 0
        update_size = 0
        update_ids = self.selected_update_ids()
        if update_ids:
            placeholders = ",".join("?" for _ in update_ids)
            update_size = sum(
                int(row["file_size"] or 0)
                for row in self.conn.execute(
                    f"SELECT file_size FROM updates WHERE id IN ({placeholders})",
                    update_ids,
                )
            )
        self.install_estimate.setText(_install_size_text(base_size, len(update_ids), update_size))

    def installed_status_text(self, game_id: int, game: dict, update_rows) -> str:
        installed = self.conn.execute(
            """
            SELECT raw_version, destination_label, destination_folder, completed_at
            FROM install_jobs
            WHERE game_id=? AND status='finished'
            ORDER BY raw_version DESC, completed_at DESC
            LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        if installed:
            where = installed["destination_label"] or _display_folder(installed["destination_folder"])
            return f"Latest Installed: {version_label(installed['raw_version'])} | {where}"
        if _path_is_install_destination(game.get("file_path") or "", self.settings.install_folder):
            versions = [file_version_number(Path(game.get("file_path") or "").name)]
            versions.extend(file_version_number(row["file_name"]) for row in update_rows)
            return f"Latest Installed: {version_label(max(versions or [0]))} | detected from catalog path"
        return "Latest Installed: None"

    def create_install_job(
        self,
        *,
        game_id: int | None,
        source_path: str,
        file_name: str,
        file_size: int,
        file_kind: str,
        detected_version: str = "",
    ) -> int:
        raw_version = file_version_number(file_name)
        self.conn.execute(
            """
            INSERT INTO install_jobs(
                game_id, source_path, destination_folder, destination_label, file_name,
                file_size, file_kind, detected_version, raw_version, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                game_id,
                source_path,
                self.settings.install_folder,
                self.settings.install_folder_label,
                file_name,
                int(file_size or 0),
                file_kind,
                detected_version,
                raw_version,
            ),
        )
        self.conn.commit()
        return int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def set_install_job_status(
        self,
        job_id: int,
        status: str,
        *,
        destination_path: str | None = None,
        error: str | None = None,
    ) -> None:
        completed = ", completed_at=CURRENT_TIMESTAMP" if status in {"finished", "failed", "sent"} else ""
        self.conn.execute(
            f"""
            UPDATE install_jobs
            SET status=?, destination_path=COALESCE(?, destination_path), error=?{completed}
            WHERE id=?
            """,
            (status, destination_path, error, job_id),
        )
        self.conn.commit()

    def process_install_items(self, items: list[dict]) -> tuple[int, list[str]]:
        if is_shell_path(self.settings.install_folder):
            return self.process_mtp_install_batch(items)
        moved = 0
        failures: list[str] = []
        for item in items:
            error = self.process_install_item(item)
            if error:
                failures.append(error)
            else:
                moved += 1
        return moved, failures

    def process_mtp_install_batch(self, items: list[dict]) -> tuple[int, list[str]]:
        if not items:
            return 0, []
        total_size = sum(int(item.get("file_size") or 0) for item in items)
        space_failure = self.install_destination_space_failure(total_size)
        if space_failure:
            return 0, [space_failure]
        missing_sources = [
            item["source_path"]
            for item in items
            if not Path(item["source_path"]).exists() and not is_shell_path(item["source_path"])
        ]
        if missing_sources:
            return 0, [f"Missing source file: {missing_sources[0]}"]
        sent = 0
        failures: list[str] = []
        for index, item in enumerate(items):
            try:
                job_id = self.create_install_job(
                    game_id=item.get("game_id"),
                    source_path=item["source_path"],
                    file_name=item["file_name"],
                    file_size=int(item.get("file_size") or 0),
                    file_kind=item["kind"],
                    detected_version=item.get("detected_version") or "",
                )
                self.set_install_job_status(job_id, "copying")
            except Exception as exc:
                failures.append(f"{item['file_name']}: could not create install record: {exc}")
                break
            try:
                destination = move_file_to_folder(item["source_path"], self.settings.install_folder)
                self.set_install_job_status(job_id, "sent", destination_path=str(destination), error=None)
                sent += 1
            except Exception as exc:
                message = f"{item['file_name']}: {exc}"
                try:
                    self.set_install_job_status(job_id, "failed", error=message)
                except Exception:
                    pass
                failures.append(message)
                break
        return sent, failures

    def start_mtp_install_items(
        self,
        items: list[dict],
        *,
        item_label: str = "file",
        reload_game_id: int | None = None,
        refresh_games_after: bool = False,
        refresh_unmatched_after: bool = False,
    ) -> None:
        if not items:
            return
        total_size = sum(int(item.get("file_size") or 0) for item in items)
        space_failure = self.install_destination_space_failure(total_size)
        if space_failure:
            QMessageBox.warning(self, "Not enough space", space_failure)
            return
        missing_sources = [
            item["source_path"]
            for item in items
            if not Path(item["source_path"]).exists() and not is_shell_path(item["source_path"])
        ]
        if missing_sources:
            QMessageBox.warning(self, "MTP install", f"Missing source file: {missing_sources[0]}")
            return

        jobs: list[tuple[dict, int]] = []
        try:
            for item in items:
                job_id = self.create_install_job(
                    game_id=item.get("game_id"),
                    source_path=item["source_path"],
                    file_name=item["file_name"],
                    file_size=int(item.get("file_size") or 0),
                    file_kind=item["kind"],
                    detected_version=item.get("detected_version") or "",
                )
                jobs.append((item, job_id))
                self.set_install_job_status(job_id, "copying")
        except Exception as exc:
            message = f"Could not create install record: {exc}"
            for _, job_id in jobs:
                self.set_install_job_status(job_id, "failed", error=message)
            QMessageBox.warning(self, "MTP install", message)
            return

        self.statusBar().showMessage(
            f"MTP install queue started for {len(items)} file(s). Windows/Switch will show transfer progress.",
            10_000,
        )
        future = self.install_executor.submit(
            _run_mtp_install_queue,
            [item["source_path"] for item in items],
            self.settings.install_folder,
        )
        self.active_install_futures.append(future)
        self.poll_mtp_install_future(
            future,
            jobs,
            item_label=item_label,
            reload_game_id=reload_game_id,
            refresh_games_after=refresh_games_after,
            refresh_unmatched_after=refresh_unmatched_after,
        )

    def poll_mtp_install_future(
        self,
        future: Future,
        jobs: list[tuple[dict, int]],
        *,
        item_label: str,
        reload_game_id: int | None,
        refresh_games_after: bool,
        refresh_unmatched_after: bool,
    ) -> None:
        if not future.done():
            QTimer.singleShot(
                1000,
                lambda: self.poll_mtp_install_future(
                    future,
                    jobs,
                    item_label=item_label,
                    reload_game_id=reload_game_id,
                    refresh_games_after=refresh_games_after,
                    refresh_unmatched_after=refresh_unmatched_after,
                ),
            )
            return
        if future in self.active_install_futures:
            self.active_install_futures.remove(future)
        try:
            results = future.result()
        except Exception as exc:
            results = [("", str(exc))]
        self.finish_mtp_install_items(
            jobs,
            results,
            item_label=item_label,
            reload_game_id=reload_game_id,
            refresh_games_after=refresh_games_after,
            refresh_unmatched_after=refresh_unmatched_after,
        )

    def finish_mtp_install_items(
        self,
        jobs: list[tuple[dict, int]],
        results: list[tuple[str, str | None]],
        *,
        item_label: str,
        reload_game_id: int | None,
        refresh_games_after: bool,
        refresh_unmatched_after: bool,
    ) -> None:
        sent = 0
        failures: list[str] = []
        for index, (item, job_id) in enumerate(jobs):
            destination, error = results[index] if index < len(results) else ("", "Not sent.")
            if error:
                message = f"{item['file_name']}: {error}"
                failures.append(message)
                self.set_install_job_status(job_id, "failed", error=message)
                continue
            self.set_install_job_status(job_id, "sent", destination_path=destination, error=None)
            sent += 1
        if reload_game_id:
            self.load_game(reload_game_id)
        if refresh_games_after:
            self.refresh_games()
        if refresh_unmatched_after:
            self.refresh_unmatched()
        self.refresh_mtp_storage_status()
        self.show_mtp_handoff_result(sent, failures, item_label=item_label)

    def process_install_item(self, item: dict) -> str | None:
        space_failure = self.install_destination_space_failure(int(item.get("file_size") or 0))
        if space_failure:
            return f"{item['file_name']}: {space_failure}"
        try:
            job_id = self.create_install_job(
                game_id=item.get("game_id"),
                source_path=item["source_path"],
                file_name=item["file_name"],
                file_size=int(item.get("file_size") or 0),
                file_kind=item["kind"],
                detected_version=item.get("detected_version") or "",
            )
        except Exception as exc:
            return f"{item['file_name']}: could not create install record: {exc}"
        try:
            self.set_install_job_status(job_id, "copying")
            if not Path(item["source_path"]).exists() and not is_shell_path(item["source_path"]):
                raise FileNotFoundError(item["source_path"])
            destination = move_file_to_folder(item["source_path"], self.settings.install_folder)
            self.apply_install_destination(item, destination)
            self.set_install_job_status(job_id, "finished", destination_path=str(destination), error=None)
            return None
        except Exception as exc:
            message = f"{item['file_name']}: {exc}"
            try:
                self.set_install_job_status(job_id, "failed", error=message)
            except Exception:
                pass
            return message

    def apply_install_destination(self, item: dict, destination: Path | str) -> None:
        if item["kind"] == "base":
            name, extension, modified = _installed_file_metadata(destination, item["file_name"])
            self.conn.execute(
                """
                UPDATE game_files
                SET file_path=?, file_name=?, file_extension=?, modified_time=?
                WHERE id=?
                """,
                (str(destination), name, extension, modified, int(item["id"])),
            )
        else:
            name, _, modified = _installed_file_metadata(destination, item["file_name"])
            self.conn.execute(
                """
                UPDATE updates
                SET file_path=?, file_name=?, modified_time=?
                WHERE id=?
                """,
                (str(destination), name, modified, int(item["id"])),
            )
        self.conn.commit()

    def show_install_result(
        self,
        title: str,
        moved: int,
        failures: list[str],
        *,
        item_label: str = "file transfer",
    ) -> None:
        if not failures:
            QMessageBox.information(self, f"{title} complete", f"Finished {moved} {item_label}(s).")
            return
        message = f"Finished {moved} {item_label}(s).\nFailed: {len(failures)}"
        message += "\n\n" + "\n".join(failures[:5])
        if len(failures) > 5:
            message += f"\n...and {len(failures) - 5} more."
        QMessageBox.warning(self, f"{title} incomplete", message)

    def show_mtp_handoff_result(
        self,
        sent: int,
        failures: list[str],
        *,
        item_label: str = "file",
    ) -> None:
        note = (
            "The app can only confirm that each file was sent to Windows/MTP. "
            "Check Windows/Switch for the actual install result."
        )
        if not failures:
            QMessageBox.information(
                self,
                "MTP files sent",
                f"Sent {sent} {item_label}(s) to Windows/MTP.\n\n{note}",
            )
            return
        message = f"Sent {sent} {item_label}(s) to Windows/MTP.\n\n{note}\n\nIssues: {len(failures)}"
        message += "\n\n" + "\n".join(failures[:5])
        if len(failures) > 5:
            message += f"\n...and {len(failures) - 5} more."
        QMessageBox.warning(self, "MTP queue stopped", message)

    def refresh_grid(self) -> None:
        if not hasattr(self, "grid_list"):
            return
        self.clear_context_highlight()
        self._refresh_grid_list(self.grid_list)
        self.refresh_favorites_grid()
        self.update_grid_item_size()

    def refresh_favorites_grid(self) -> None:
        if not hasattr(self, "favorites_grid_list"):
            return
        self._refresh_grid_list(self.favorites_grid_list, favorites_only=True)
        self.update_grid_item_size()

    def _refresh_grid_list(self, grid_list: QListWidget, *, favorites_only: bool = False) -> None:
        grid_list.clear()
        query = """
            SELECT id, display_title, cover_image_url, favorite
            FROM games
        """
        if favorites_only:
            query += " WHERE favorite=1"
        query += " ORDER BY display_title COLLATE NOCASE"
        for row in self.conn.execute(query):
            item = QListWidgetItem(row["display_title"])
            item.setData(Qt.UserRole, row["id"])
            cover_url = _higher_res_image_url(row["cover_image_url"] or "")
            item.setData(Qt.UserRole + 1, cover_url)
            item.setData(Qt.UserRole + 2, bool(row["favorite"]))
            item.setSizeHint(self._grid_item_size())
            grid_list.addItem(item)
            if cover_url:
                self._load_list_icon(cover_url, item, self._grid_icon_size(), favorite=bool(row["favorite"]))

    def update_grid_item_size(self) -> None:
        if not hasattr(self, "grid_list"):
            return
        icon_size = self._grid_icon_size()
        for grid_list in (self.grid_list, getattr(self, "favorites_grid_list", None)):
            if grid_list is None:
                continue
            grid_list.setIconSize(icon_size)
            item_size = self._grid_item_size()
            grid_list.setGridSize(item_size)
            for index in range(grid_list.count()):
                item = grid_list.item(index)
                item.setSizeHint(item_size)
                url = item.data(Qt.UserRole + 1)
                if url:
                    self._load_list_icon(str(url), item, icon_size, favorite=bool(item.data(Qt.UserRole + 2)))
        if hasattr(self, "grid_size_label"):
            self.grid_size_label.setText(f"{icon_size.width()} px")

    def _grid_icon_size(self) -> QSize:
        width = self.grid_size.value() if hasattr(self, "grid_size") else 170
        return QSize(width, int(width * 1.45))

    def _grid_item_size(self) -> QSize:
        icon_size = self._grid_icon_size()
        return QSize(icon_size.width() + 64, icon_size.height() + 98)

    def open_grid_game(self, item: QListWidgetItem) -> None:
        game_id = int(item.data(Qt.UserRole))
        self.show_game_in_library(game_id)

    def open_grid_menu(self, position) -> None:
        self._open_grid_menu(self.grid_list, position)

    def open_favorites_grid_menu(self, position) -> None:
        self._open_grid_menu(self.favorites_grid_list, position)

    def _open_grid_menu(self, grid_list: QListWidget, position) -> None:
        item = grid_list.itemAt(position)
        if item is None:
            return
        grid_list.setFocus()
        grid_list.setCurrentItem(item)
        item.setSelected(True)
        self.mark_context_item(item)
        QApplication.processEvents()
        game_id = int(item.data(Qt.UserRole))
        favorite = bool(item.data(Qt.UserRole + 2))
        menu = QMenu(self)
        open_action = menu.addAction("Open in library")
        favorite_action = menu.addAction("Remove favorite" if favorite else "Favorite game")
        mark_dlc_action = menu.addAction("Mark as DLC/update")
        backup_action = menu.addAction("Export catalog backup")
        chosen = menu.exec(grid_list.mapToGlobal(position))
        if chosen == open_action:
            self.show_game_in_library(game_id)
        elif chosen == favorite_action:
            self.toggle_favorite(game_id)
        elif chosen == mark_dlc_action:
            self.mark_game_as_update(game_id)
        elif chosen == backup_action:
            self.export_catalog_backup()

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
        if hasattr(self, "updates_only"):
            self.updates_only.setChecked(False)
        self.refresh_games()
        for index in range(self.games_list.count()):
            item = self.games_list.item(index)
            if int(item.data(Qt.UserRole)) == game_id:
                self.games_list.setCurrentItem(item)
                break
        self.load_game(game_id)
        if hasattr(self, "tabs"):
            self.tabs.setCurrentWidget(self.library_tab_widget)

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
        favorite = self.is_favorite(int(item.data(Qt.UserRole)))
        favorite_action = menu.addAction("Remove favorite" if favorite else "Favorite game")
        mark_dlc_action = menu.addAction("Mark as DLC/update")
        backup_action = menu.addAction("Export catalog backup")
        delete_action = menu.addAction("Delete game file from disk")
        chosen = menu.exec(self.games_list.mapToGlobal(position))
        if chosen == search_action:
            self.search_metadata_match(int(item.data(Qt.UserRole)))
        elif chosen == favorite_action:
            self.toggle_favorite(int(item.data(Qt.UserRole)))
        elif chosen == mark_dlc_action:
            self.mark_game_as_update(int(item.data(Qt.UserRole)))
        elif chosen == backup_action:
            self.export_catalog_backup()
        elif chosen == delete_action:
            self.delete_game(int(item.data(Qt.UserRole)))

    def export_catalog_backup(self) -> None:
        default_name = f"switch_catalog_backup_{time.strftime('%Y%m%d_%H%M%S')}.sqlite"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export catalog backup",
            default_name,
            "SQLite database (*.sqlite *.db);;All files (*)",
        )
        if not path:
            return
        try:
            destination = sqlite3.connect(path)
            try:
                self.conn.backup(destination)
            finally:
                destination.close()
            QMessageBox.information(self, "Backup complete", f"Exported catalog backup to:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Backup failed", str(exc))

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
        trailer = ""
        if game.get("trailer_url"):
            trailer = (
                '<br><a style="color:#ff5555; text-decoration: underline;" '
                f'href="{html.escape(game["trailer_url"], quote=True)}">Trailer</a>'
            )
        self.meta.setText(
            "Release: "
            f"{html.escape(game.get('release_date') or 'Unknown')} | "
            f"Developer: {html.escape(game.get('developer') or 'Unknown')} | "
            f"Publisher: {html.escape(game.get('publisher') or 'Unknown')}<br>"
            f"Genres: {html.escape(genres or 'Unknown')}"
            f"{trailer}"
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
            """
            SELECT id, file_path, file_name, detected_version, file_size, match_confidence
            FROM updates
            WHERE game_id=?
            ORDER BY file_name
            """,
            (game_id,),
        ).fetchall()
        grouped_updates = {
            "Updates": [row for row in update_rows if _update_file_group(row["file_name"]) == "Updates"],
            "DLC": [row for row in update_rows if _update_file_group(row["file_name"]) == "DLC"],
        }
        for group_name, rows in grouped_updates.items():
            if not rows:
                continue
            header = QListWidgetItem(group_name)
            header.setFlags(Qt.ItemIsEnabled)
            header.setForeground(QBrush(QColor("#8be9fd")))
            self.updates.addItem(header)
            for update in rows:
                version = _detected_version_suffix(update["detected_version"] or "")
                item = QListWidgetItem(f"{update['file_name']}{version}")
                item.setData(Qt.UserRole, update["id"])
                self.updates.addItem(item)
        self.refresh_versions()
        status, newer_versions = update_status(
            Path(game.get("file_path") or "").name,
            [row["file_name"] for row in update_rows],
            self.versions,
        )
        self.version_status.setText(status)
        self.installed_status.setText(self.installed_status_text(game_id, game, update_rows))
        self.newer_updates.clear()
        for version in newer_versions:
            self.newer_updates.addItem(released_version_label(version.version, version.release_date))
        self.newer_updates_label.setVisible(bool(newer_versions))
        self.newer_updates.setVisible(bool(newer_versions))
        self.update_install_estimate()

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
        scaled = _fixed_size_pixmap(pixmap, size)
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

    def open_trailer(self, url: str) -> None:
        if not url:
            return
        dialog = TrailerDialog(url, self)
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
        return [int(value) for item in self.updates.selectedItems() if (value := item.data(Qt.UserRole)) is not None]

    def selected_unmatched_update_ids(self) -> list[int]:
        return [int(item.data(Qt.UserRole)) for item in self.unmatched.selectedItems()]

    def install_selected_game(self) -> None:
        if self.current_game_id is None:
            return
        if not self.ensure_install_destination_configured():
            return
        base_row = self.conn.execute(
            """
            SELECT id, file_path, file_name, file_size
            FROM game_files
            WHERE game_id=? AND is_base_game=1
            ORDER BY id LIMIT 1
            """,
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
                f"""
                SELECT id, file_path, file_name, detected_version, file_size
                FROM updates
                WHERE id IN ({placeholders})
                ORDER BY file_name
                """,
                update_ids,
            ).fetchall()
        install_folder = _display_folder(self.settings.install_folder, self.settings.install_folder_label)
        total_size = int(base_row["file_size"] or 0) + sum(int(row["file_size"] or 0) for row in update_rows)
        if not self.ensure_install_destination_has_space(total_size):
            return
        message = (
            f"Move the base game first, then {len(update_rows)} selected update/DLC file(s), into:\n"
            f"{install_folder}\n\nTotal install size: {_format_bytes(total_size)}"
        )
        if QMessageBox.question(self, "Install files", message) != QMessageBox.Yes:
            return
        items = [
            {
                "kind": "base",
                "game_id": self.current_game_id,
                "id": int(base_row["id"]),
                "source_path": base_row["file_path"],
                "file_name": base_row["file_name"],
                "file_size": int(base_row["file_size"] or 0),
                "detected_version": "",
            }
        ]
        items.extend(
            {
                "kind": _update_file_group(update["file_name"]).lower(),
                "game_id": self.current_game_id,
                "id": int(update["id"]),
                "source_path": update["file_path"],
                "file_name": update["file_name"],
                "file_size": int(update["file_size"] or 0),
                "detected_version": update["detected_version"] or "",
            }
            for update in update_rows
        )
        if is_shell_path(self.settings.install_folder):
            self.start_mtp_install_items(
                items,
                reload_game_id=self.current_game_id,
                refresh_games_after=True,
            )
            return
        moved, failures = self.process_install_items(items)
        self.load_game(self.current_game_id)
        self.refresh_games()
        if is_shell_path(self.settings.install_folder):
            self.refresh_mtp_storage_status()
            self.show_mtp_handoff_result(moved, failures)
        else:
            self.show_install_result("Install", moved, failures)

    def install_selected_updates_only(self) -> None:
        if not self.ensure_install_destination_configured():
            return
        update_ids = self.selected_update_ids()
        if not update_ids and hasattr(self, "unmatched"):
            update_ids = self.selected_unmatched_update_ids()
        if not update_ids:
            return
        placeholders = ",".join("?" for _ in update_ids)
        rows = self.conn.execute(
            f"""
            SELECT id, game_id, file_path, file_name, detected_version, file_size
            FROM updates
            WHERE id IN ({placeholders})
            ORDER BY file_name
            """,
            update_ids,
        ).fetchall()
        total_size = sum(int(row["file_size"] or 0) for row in rows)
        if not self.ensure_install_destination_has_space(total_size):
            return
        if QMessageBox.question(
            self,
            "Install update/DLC files",
            "Move "
            f"{len(rows)} selected update/DLC file(s) into:\n"
            f"{_display_folder(self.settings.install_folder, self.settings.install_folder_label)}"
            f"\n\nTotal install size: {_format_bytes(total_size)}",
        ) != QMessageBox.Yes:
            return
        items = [
            {
                "kind": _update_file_group(row["file_name"]).lower(),
                "game_id": row["game_id"],
                "id": int(row["id"]),
                "source_path": row["file_path"],
                "file_name": row["file_name"],
                "file_size": int(row["file_size"] or 0),
                "detected_version": row["detected_version"] or "",
            }
            for row in rows
        ]
        if is_shell_path(self.settings.install_folder):
            self.start_mtp_install_items(
                items,
                item_label="update/DLC file",
                reload_game_id=self.current_game_id,
                refresh_unmatched_after=True,
            )
            return
        moved, failures = self.process_install_items(items)
        if self.current_game_id:
            self.load_game(self.current_game_id)
        self.refresh_unmatched()
        if is_shell_path(self.settings.install_folder):
            self.refresh_mtp_storage_status()
            self.show_mtp_handoff_result(moved, failures, item_label="update/DLC file")
        else:
            self.show_install_result("Install", moved, failures, item_label="update/DLC file")

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
            self.tabs.setCurrentWidget(self.unmatched_tab_widget)

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
        reset_library_cache(self.conn)
        summary = scan_library(
            self.conn,
            self.settings.base_games_folder,
            self.settings.updates_folder,
            recursive=self.settings.scan_recursively,
            threshold=self.settings.fuzzy_match_threshold,
        )
        self.versions = load_versions()
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
                OR trailer_url IS NULL
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
            self._sync_http_server(show_errors=True)
            self.refresh_match_games()

    def _sync_http_server(self, *, show_errors: bool) -> None:
        if not self.settings.http_server_enabled:
            self.catalog_server.stop()
            self.statusBar().showMessage("Catalog HTTP server stopped", 4000)
            return
        try:
            self.catalog_server.start(self.settings)
        except OSError as exc:
            self.catalog_server.stop()
            message = f"Could not start catalog HTTP server on port {self.settings.http_server_port}: {exc}"
            if show_errors:
                QMessageBox.warning(self, "HTTP server", message)
            self.statusBar().showMessage(message, 8000)
            return
        self.statusBar().showMessage(f"Catalog HTTP server running at {switch_directory_url(self.catalog_server.port)}", 8000)

    def closeEvent(self, event) -> None:
        self.install_executor.shutdown(wait=False, cancel_futures=True)
        self.catalog_server.stop()
        super().closeEvent(event)

    def check_for_app_updates(self, *, silent: bool = False) -> None:
        try:
            info = check_latest_release()
        except Exception as exc:
            if not silent:
                QMessageBox.warning(self, "Update check failed", str(exc))
            return
        if info.update_available:
            message = (
                f"{info.release_name} is available.\n\n"
                f"Installed: v{info.current_version}\n"
                f"Latest: {info.latest_version}"
            )
            if QMessageBox.information(
                self,
                "Update available",
                message,
                QMessageBox.Open | QMessageBox.Close,
                QMessageBox.Open,
            ) == QMessageBox.Open:
                QDesktopServices.openUrl(QUrl(info.release_url or RELEASES_PAGE_URL))
        elif not silent:
            QMessageBox.information(
                self,
                "No update available",
                f"Switch Game Catalog is up to date.\n\nInstalled: v{info.current_version}",
            )

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
        self.sync_unmatched_tab_visibility()

    def sync_unmatched_tab_visibility(self) -> None:
        if not hasattr(self, "tabs") or not hasattr(self, "unmatched_tab_widget") or not hasattr(self, "unmatched"):
            return
        index = self.tabs.indexOf(self.unmatched_tab_widget)
        has_unmatched = self.unmatched.count() > 0
        if has_unmatched and index < 0:
            self.tabs.addTab(self.unmatched_tab_widget, "Unmatched Updates")
        elif not has_unmatched and index >= 0:
            if self.tabs.currentWidget() == self.unmatched_tab_widget:
                self.tabs.setCurrentWidget(self.library_tab_widget)
            self.tabs.removeTab(index)

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


class TrailerDialog(QDialog):
    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trailer")
        self.resize(960, 600)
        layout = QVBoxLayout(self)
        if QWebEngineView is None:
            message = QLabel("Trailer playback is unavailable in this build.")
            message.setAlignment(Qt.AlignCenter)
            layout.addWidget(message, 1)
            open_browser = QPushButton("Open Trailer")
            open_browser.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            layout.addWidget(open_browser)
        else:
            self.player = QWebEngineView()
            self.player.load(QUrl(_youtube_player_url(url)))
            layout.addWidget(self.player, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


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
        self.resize(680, 520)
        layout = QFormLayout(self)
        self.base = _folder_field(settings.base_games_folder)
        self.updates = _folder_field(settings.updates_folder)
        self.install_destination = QComboBox()
        self.install_destination.addItem("Local Folder", "local")
        self.install_destination.addItem("NAND Install", "nand")
        self.install_destination.addItem("SD Card Install", "sd")
        install_destination = getattr(settings, "install_destination", "local")
        index = self.install_destination.findData(install_destination)
        if index < 0:
            index = 0
        self.install_destination.setCurrentIndex(index)
        self.install = _folder_field(
            settings.install_folder if install_destination == "local" else "",
            display_value="" if install_destination != "local" else settings.install_folder_label,
        )
        self.install_destination.currentIndexChanged.connect(self.update_install_destination_mode)
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
        self.auto_check_updates = QCheckBox()
        self.auto_check_updates.setChecked(settings.auto_check_updates_on_startup)
        self.http_enabled = QCheckBox()
        self.http_enabled.setChecked(settings.http_server_enabled)
        self.http_port = QSpinBox()
        self.http_port.setRange(1, 65535)
        self.http_port.setValue(int(settings.http_server_port or 8000))
        self.http_username = QLineEdit(settings.http_server_username)
        self.http_password = QLineEdit(settings.http_server_password)
        self.http_password.setEchoMode(QLineEdit.Password)
        self.http_url = QLabel()
        self.http_url.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.http_port.valueChanged.connect(self.update_http_url)
        self.update_http_url()
        update_controls = QHBoxLayout()
        check_updates = QPushButton("Check for Updates")
        check_updates.clicked.connect(self.check_for_updates)
        update_controls.addWidget(self.auto_check_updates)
        update_controls.addWidget(check_updates)
        update_controls.addStretch(1)
        layout.addRow("Base games folder", self.base)
        layout.addRow("Updates folder", self.updates)
        layout.addRow("Install destination", self.install_destination)
        layout.addRow("Local install folder", self.install)
        layout.addRow("Metadata provider", self.provider)
        layout.addRow("IGDB client ID", self.igdb_client_id)
        layout.addRow("IGDB client secret", self.igdb_client_secret)
        layout.addRow("Scan recursively", self.recursive)
        layout.addRow("Auto-rescan on startup", self.auto)
        layout.addRow("Enable HTTP server", self.http_enabled)
        layout.addRow("HTTP port", self.http_port)
        layout.addRow("HTTP username", self.http_username)
        layout.addRow("HTTP password", self.http_password)
        layout.addRow("Switch URL", self.http_url)
        layout.addRow("App updates", update_controls)
        actions = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addStretch(1)
        actions.addWidget(save)
        actions.addWidget(cancel)
        layout.addRow(actions)
        version_label = QLabel(f"Switch Game Catalog v{__version__}")
        version_label.setAlignment(Qt.AlignRight)
        layout.addRow(version_label)
        self.update_install_destination_mode()

    def accept(self) -> None:
        self.settings.base_games_folder = normalize_folder(self.base.text())
        self.settings.updates_folder = normalize_folder(self.updates.text())
        self.settings.install_destination = self.install_destination.currentData() or "local"
        if self.settings.install_destination == "local":
            self.settings.install_folder = normalize_folder(self.install.text())
            self.settings.install_folder_label = (
                self.install.display_text() if is_shell_path(self.settings.install_folder) else ""
            )
        else:
            self.settings.install_folder = ""
            self.settings.install_folder_label = mtp_install_destination_label(self.settings.install_destination)
        self.settings.metadata_provider = self.provider.currentText()
        self.settings.igdb_client_id = self.igdb_client_id.text().strip()
        self.settings.igdb_client_secret = self.igdb_client_secret.text().strip()
        self.settings.scan_recursively = self.recursive.isChecked()
        self.settings.auto_rescan_on_startup = self.auto.isChecked()
        self.settings.auto_check_updates_on_startup = self.auto_check_updates.isChecked()
        self.settings.http_server_enabled = self.http_enabled.isChecked()
        self.settings.http_server_port = int(self.http_port.value())
        self.settings.http_server_username = self.http_username.text().strip()
        self.settings.http_server_password = self.http_password.text()
        super().accept()

    def update_install_destination_mode(self) -> None:
        is_local = self.install_destination.currentData() == "local"
        self.install.setEnabled(is_local)

    def update_http_url(self) -> None:
        self.http_url.setText(switch_directory_url(int(self.http_port.value())))

    def check_for_updates(self) -> None:
        parent = self.parent()
        if hasattr(parent, "check_for_app_updates"):
            parent.check_for_app_updates(silent=False)


def _folder_field(value: str, *, display_value: str = "", shell_browse: bool = False) -> QWidget:
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    state = {
        "value": value,
        "display": _display_folder(value, display_value),
    }
    line = QLineEdit(state["display"])
    if state["display"] != state["value"]:
        line.setToolTip(state["value"])
    browse = QPushButton("Browse")

    def set_value(new_value: str, new_display: str = "") -> None:
        state["value"] = new_value
        state["display"] = _display_folder(new_value, new_display)
        line.setText(state["display"])
        line.setToolTip(new_value if state["display"] != new_value else "")

    def text() -> str:
        current = line.text().strip()
        if current == state["display"]:
            return state["value"]
        return current

    def display_text() -> str:
        return _display_folder(text(), line.text().strip())

    def choose() -> None:
        start_folder = "" if is_shell_path(text()) else text()
        folder = QFileDialog.getExistingDirectory(wrapper, "Choose folder", start_folder)
        if folder:
            set_value(folder)

    browse.clicked.connect(choose)
    layout.addWidget(line, 1)
    layout.addWidget(browse)
    if shell_browse:
        browse_mtp = QPushButton("Browse MTP")

        def choose_mtp() -> None:
            folder, folder_label = _choose_shell_folder(wrapper, "Choose Switch install folder")
            if folder:
                set_value(folder, folder_label)

        browse_mtp.clicked.connect(choose_mtp)
        layout.addWidget(browse_mtp)
    wrapper.text = text  # type: ignore[attr-defined]
    wrapper.display_text = display_text  # type: ignore[attr-defined]
    return wrapper


def _run_mtp_install_queue(source_paths: list[str], install_folder: str) -> list[tuple[str, str | None]]:
    results: list[tuple[str, str | None]] = []
    for index, source_path in enumerate(source_paths):
        try:
            destination = move_file_to_folder(source_path, install_folder)
        except Exception as exc:
            results.append(("", str(exc)))
            break
        results.append((str(destination), None))
        if index < len(source_paths) - 1:
            time.sleep(1)
    return results


def _display_folder(value: str, display_value: str = "") -> str:
    if display_value and is_shell_path(value):
        return display_value
    return value


def _path_is_install_destination(path: str, install_folder: str) -> bool:
    if not path or not install_folder:
        return False
    if is_shell_path(path) or is_shell_path(install_folder):
        return path.lower().startswith(install_folder.lower())
    try:
        Path(path).resolve().relative_to(Path(install_folder).resolve())
    except (OSError, ValueError):
        return False
    return True


def _detected_version_suffix(detected: str) -> str:
    if not detected:
        return ""
    raw_version = _detected_raw_version(detected)
    dotted = detected if "." in detected else _compact_dotted_version(raw_version_to_dotted(raw_version))
    return f" (v{raw_version}) (v{dotted})"


def _detected_raw_version(detected: str) -> int:
    if "." in detected:
        parts = [int(part) for part in detected.split(".")]
        parts.extend([0] * (3 - len(parts)))
        major, minor, patch = parts[:3]
        return (major * 65536) + (minor * 256) + patch
    value = int(detected)
    if value and value < 65536:
        return value * 65536
    return value


def _compact_dotted_version(dotted: str) -> str:
    parts = dotted.split(".")
    while len(parts) > 2 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def _update_file_group(filename: str) -> str:
    if "dlc" in Path(filename).stem.lower():
        return "DLC"
    title_id = extract_title_id(filename)
    if title_id:
        try:
            content_type = int(title_id, 16) & 0xFFF
        except ValueError:
            content_type = 0
        if content_type and content_type != 0x800:
            return "DLC"
    return "Updates"


def _youtube_player_url(url: str) -> str:
    if "youtube.com/watch" in url:
        return url
    if "youtube.com/embed/" in url:
        video_id = url.rsplit("/", 1)[-1].split("?", 1)[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    if "youtu.be/" in url:
        video_id = url.rsplit("/", 1)[-1].split("?", 1)[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return url


def _youtube_embed_url(url: str) -> str:
    if "youtube.com/watch" in url:
        query = QUrl(url).query()
        video_id = ""
        for part in query.split("&"):
            if part.startswith("v="):
                video_id = part.removeprefix("v=")
                break
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}"
    if "youtu.be/" in url:
        video_id = url.rsplit("/", 1)[-1].split("?", 1)[0]
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}"
    return url


def _choose_shell_folder(parent: QWidget, title: str) -> tuple[str, str]:
    script = r"""
$ErrorActionPreference = 'Stop'
$title = $env:SWITCH_CATALOG_MTP_PICKER_TITLE
$shell = New-Object -ComObject Shell.Application
$folder = $shell.BrowseForFolder(0, $title, 0x00000040, 17)
if ($null -eq $folder) {
    exit 2
}
$path = $folder.Self.Path
if ([string]::IsNullOrWhiteSpace($path)) {
    throw "Selected folder did not provide a Shell path."
}
if ($path.StartsWith("::{")) {
    $path = "shell:$path"
}
function Get-FriendlyPath($folder) {
    $names = New-Object System.Collections.Generic.List[string]
    $current = $folder
    while ($null -ne $current -and $null -ne $current.Self) {
        $name = $current.Self.Name
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            $names.Insert(0, $name)
        }
        $parent = $current.ParentFolder
        if ($null -eq $parent -or $null -eq $parent.Self) {
            break
        }
        if ($parent.Self.Path -eq $current.Self.Path) {
            break
        }
        $current = $parent
    }
    while ($names.Count -gt 0 -and ($names[0] -eq "Desktop" -or $names[0] -eq "This PC")) {
        $names.RemoveAt(0)
    }
    if ($names.Count -eq 0) {
        return $folder.Self.Name
    }
    return ($names -join "/")
}
$label = Get-FriendlyPath $folder
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[PSCustomObject]@{ path = $path; label = $label } | ConvertTo-Json -Compress
"""
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    env["SWITCH_CATALOG_MTP_PICKER_TITLE"] = title
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_script,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        QMessageBox.warning(parent, "MTP folder picker", str(exc))
        return "", ""
    if result.returncode == 2:
        return "", ""
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        QMessageBox.warning(parent, "MTP folder picker", message or "Could not open the Windows Shell folder picker.")
        return "", ""
    output = result.stdout.strip()
    if not output:
        return "", ""
    try:
        selected = json.loads(output.splitlines()[-1])
    except json.JSONDecodeError:
        return output.splitlines()[-1], ""
    return selected.get("path", ""), selected.get("label", "")


def _installed_file_metadata(destination: Path | str, original_name: str) -> tuple[str, str, float]:
    if isinstance(destination, Path):
        return destination.name, destination.suffix.lower(), destination.stat().st_mtime
    name = original_name or str(destination).rsplit("\\", 1)[-1]
    return name, Path(name).suffix.lower(), time.time()


def _metadata_ready(settings: AppSettings) -> bool:
    return bool(settings.igdb_client_id and settings.igdb_client_secret)


def _favorite_pixmap(pixmap: QPixmap) -> QPixmap:
    framed = QPixmap(pixmap.size())
    framed.fill(Qt.transparent)
    painter = QPainter(framed)
    painter.drawPixmap(0, 0, pixmap)
    pen = QPen(QColor("#ff79c6"), 5)
    painter.setPen(pen)
    painter.drawRoundedRect(2, 2, framed.width() - 4, framed.height() - 4, 8, 8)
    painter.end()
    return framed


def _fixed_size_pixmap(pixmap: QPixmap, size: QSize) -> QPixmap:
    canvas = QPixmap(size)
    canvas.fill(Qt.transparent)
    scaled = pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(canvas)
    painter.drawPixmap((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


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


def _install_size_text(base_size: int, selected_update_count: int, selected_update_size: int) -> str:
    total = int(base_size or 0) + int(selected_update_size or 0)
    return (
        f"Install size: Base {_format_bytes(int(base_size or 0))} + "
        f"{selected_update_count} update/DLC file(s) {_format_bytes(int(selected_update_size or 0))} = "
        f"Total size {_format_bytes(total)}"
    )

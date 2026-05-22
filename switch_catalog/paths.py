from __future__ import annotations

import os
import shutil
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
APP_NAME = "Switch Game Catalog"
LEGACY_APP_DIR = Path.home() / ".switch_library_catalog"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
DB_PATH = APP_DIR / "library.sqlite3"
SETTINGS_PATH = APP_DIR / "settings.json"
IMAGE_CACHE_DIR = APP_DIR / "images"
VERSIONS_CACHE_PATH = APP_DIR / "versions.json"
VERSIONS_TXT_CACHE_PATH = APP_DIR / "versions.txt"
BUNDLED_ICON_PATH = PACKAGE_DIR / "assets" / "switch_game_catalog.png"


def ensure_app_dirs() -> None:
    _migrate_legacy_cache()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_cache() -> None:
    if not LEGACY_APP_DIR.exists() or LEGACY_APP_DIR == APP_DIR:
        return
    APP_DIR.parent.mkdir(parents=True, exist_ok=True)
    if not APP_DIR.exists():
        try:
            shutil.move(str(LEGACY_APP_DIR), str(APP_DIR))
            return
        except OSError:
            pass
    APP_DIR.mkdir(parents=True, exist_ok=True)
    _move_if_missing(LEGACY_APP_DIR / "library.sqlite3", DB_PATH)
    _move_if_missing(LEGACY_APP_DIR / "settings.json", SETTINGS_PATH)
    _move_if_missing(LEGACY_APP_DIR / "versions.json", VERSIONS_CACHE_PATH)
    _move_if_missing(LEGACY_APP_DIR / "versions.txt", VERSIONS_TXT_CACHE_PATH)
    _move_images_if_missing()


def _move_if_missing(source: Path, destination: Path) -> None:
    if source.exists() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(destination))
        except OSError:
            pass


def _move_images_if_missing() -> None:
    legacy_images = LEGACY_APP_DIR / "images"
    if not legacy_images.exists():
        return
    if not IMAGE_CACHE_DIR.exists():
        try:
            shutil.move(str(legacy_images), str(IMAGE_CACHE_DIR))
        except OSError:
            pass
        return
    for source in legacy_images.iterdir():
        destination = IMAGE_CACHE_DIR / source.name
        _move_if_missing(source, destination)

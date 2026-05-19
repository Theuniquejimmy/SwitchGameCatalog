from __future__ import annotations

from pathlib import Path


APP_DIR = Path.home() / ".switch_library_catalog"
DB_PATH = APP_DIR / "library.sqlite3"
SETTINGS_PATH = APP_DIR / "settings.json"
IMAGE_CACHE_DIR = APP_DIR / "images"
VERSIONS_CACHE_PATH = APP_DIR / "versions.json"
VERSIONS_TXT_CACHE_PATH = APP_DIR / "versions.txt"


def ensure_app_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import SETTINGS_PATH, ensure_app_dirs


@dataclass
class AppSettings:
    base_games_folder: str = ""
    updates_folder: str = ""
    install_folder: str = ""
    metadata_provider: str = "igdb"
    igdb_client_id: str = ""
    igdb_client_secret: str = ""
    scan_recursively: bool = True
    auto_rescan_on_startup: bool = False
    auto_check_updates_on_startup: bool = True
    cache_images: bool = True
    fuzzy_match_threshold: float = 0.82


def load_settings() -> AppSettings:
    ensure_app_dirs()
    if not SETTINGS_PATH.exists():
        return AppSettings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    allowed = AppSettings.__dataclass_fields__.keys()
    settings = AppSettings(**{key: value for key, value in data.items() if key in allowed})
    settings.metadata_provider = "igdb"
    return settings


def save_settings(settings: AppSettings) -> None:
    ensure_app_dirs()
    SETTINGS_PATH.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def normalize_folder(value: str) -> str:
    if not value:
        return ""
    value = value.strip()
    if value.lower().startswith("shell:::"):
        return value
    if value.startswith("::{"):
        return f"shell:{value}"
    return str(Path(value).expanduser())

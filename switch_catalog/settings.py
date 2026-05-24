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
    install_folder_label: str = ""
    install_destination: str = "local"
    http_server_enabled: bool = False
    http_server_port: int = 8000
    http_server_username: str = ""
    http_server_password: str = ""
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
    if settings.install_destination not in {"local", "nand", "sd"}:
        settings.install_destination = "local"
    if "install_destination" not in data and _looks_like_shell_path(settings.install_folder):
        settings.install_destination = _infer_install_destination(settings.install_folder_label)
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


def _looks_like_shell_path(value: str) -> bool:
    text = (value or "").strip().lower()
    return text.startswith("shell:::") or text.startswith("::{")


def _infer_install_destination(label: str) -> str:
    lowered = (label or "").casefold()
    if "nand" in lowered:
        return "nand"
    if "sd" in lowered:
        return "sd"
    return "local"

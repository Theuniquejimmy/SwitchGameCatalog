from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .filename import detect_version, extract_title_id
from .paths import VERSIONS_CACHE_PATH, VERSIONS_TXT_CACHE_PATH, ensure_app_dirs

VERSIONS_URL = "https://raw.githubusercontent.com/blawar/titledb/master/versions.json"
VERSIONS_TXT_URL = "https://raw.githubusercontent.com/blawar/titledb/master/versions.txt"


@dataclass
class VersionInfo:
    version: int
    release_date: str


def load_versions(*, refresh: bool = False) -> dict[str, dict[str, str]]:
    ensure_app_dirs()
    versions = _load_json_versions(refresh=refresh)
    txt_versions = _load_txt_versions(refresh=refresh)
    return _merge_versions(versions, txt_versions)


def refresh_versions_if_stale(versions: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    if versions_cache_is_stale():
        return load_versions()
    return versions


def versions_cache_is_stale() -> bool:
    return _cache_is_stale(VERSIONS_CACHE_PATH) or _cache_is_stale(VERSIONS_TXT_CACHE_PATH)


def _load_json_versions(*, refresh: bool = False) -> dict[str, dict[str, str]]:
    if refresh or _cache_is_stale(VERSIONS_CACHE_PATH):
        try:
            response = requests.get(VERSIONS_URL, timeout=20)
            response.raise_for_status()
            VERSIONS_CACHE_PATH.write_text(response.text, encoding="utf-8")
        except requests.RequestException:
            if not VERSIONS_CACHE_PATH.exists():
                return {}
    try:
        return json.loads(VERSIONS_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_txt_versions(*, refresh: bool = False) -> dict[str, dict[str, str]]:
    if refresh or _cache_is_stale(VERSIONS_TXT_CACHE_PATH):
        try:
            response = requests.get(VERSIONS_TXT_URL, timeout=20)
            response.raise_for_status()
            VERSIONS_TXT_CACHE_PATH.write_text(response.text, encoding="utf-8")
        except requests.RequestException:
            if not VERSIONS_TXT_CACHE_PATH.exists():
                return {}
    try:
        return parse_versions_txt(VERSIONS_TXT_CACHE_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {}


def parse_versions_txt(text: str) -> dict[str, dict[str, str]]:
    rows: dict[str, int] = {}
    for line in text.splitlines():
        if not line or line.startswith("id|"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        title_id = parts[0].strip().upper()
        version_text = parts[2].strip()
        if len(title_id) != 16 or not version_text:
            continue
        try:
            version = int(version_text)
        except ValueError:
            continue
        base_id = _base_id_from_update_id(title_id)
        rows[title_id] = max(rows.get(title_id, 0), version)
        rows[base_id] = max(rows.get(base_id, 0), version)
    return {title_id: {str(version): ""} for title_id, version in rows.items()}


def _merge_versions(
    json_versions: dict[str, dict[str, str]],
    txt_versions: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    merged = {title_id.upper(): dict(records) for title_id, records in json_versions.items()}
    for title_id, records in txt_versions.items():
        target = merged.setdefault(title_id.upper(), {})
        for version, release_date in records.items():
            target.setdefault(version, release_date)
    return merged


def latest_for_title(versions: dict[str, dict[str, str]], title_id: str) -> VersionInfo | None:
    records = versions.get(title_id.upper())
    if not records:
        return None
    latest = max(int(version) for version in records)
    return VersionInfo(latest, records[str(latest)])


def file_version_number(filename: str) -> int:
    detected = detect_version(filename)
    if not detected or "." in detected:
        return 0
    value = int(detected)
    if value and value < 65536:
        return value * 65536
    return value


def update_status(base_filename: str, update_filenames: list[str], versions: dict[str, dict[str, str]]) -> tuple[str, list[VersionInfo]]:
    title_id = extract_title_id(base_filename)
    latest = latest_for_title(versions, title_id)
    if latest is None:
        return "Latest version: unknown", []

    local_versions = [file_version_number(base_filename)]
    local_versions.extend(file_version_number(filename) for filename in update_filenames)
    current = max(local_versions or [0])
    available = _newer_versions(versions, title_id, current)
    if not available:
        return f"Latest version installed: v{latest.version} ({latest.release_date})", []
    status = f"Installed version: v{current or 0}. Latest: v{latest.version} ({latest.release_date})"
    return status, available


def _newer_versions(versions: dict[str, dict[str, str]], title_id: str, current: int) -> list[VersionInfo]:
    records = versions.get(title_id.upper()) or {}
    return [
        VersionInfo(int(version), release_date)
        for version, release_date in sorted(records.items(), key=lambda item: int(item[0]))
        if int(version) > current
    ]


def _base_id_from_update_id(title_id: str) -> str:
    try:
        value = int(title_id, 16)
    except ValueError:
        return title_id
    if value & 0x800:
        value -= 0x800
    return f"{value:016X}"


def _cache_is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    modified = datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.now() - modified > timedelta(days=1)

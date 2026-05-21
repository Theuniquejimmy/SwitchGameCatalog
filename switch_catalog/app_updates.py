from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from . import __version__

RELEASES_API_URL = "https://api.github.com/repos/Theuniquejimmy/SwitchGameCatalog/releases/latest"
RELEASES_PAGE_URL = "https://github.com/Theuniquejimmy/SwitchGameCatalog/releases"


@dataclass
class AppUpdateInfo:
    current_version: str
    latest_version: str
    release_name: str
    release_url: str
    update_available: bool


def check_latest_release() -> AppUpdateInfo:
    response = requests.get(
        RELEASES_API_URL,
        headers={"Accept": "application/vnd.github+json"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    latest = str(payload.get("tag_name") or payload.get("name") or "").strip()
    release_url = str(payload.get("html_url") or RELEASES_PAGE_URL)
    release_name = str(payload.get("name") or latest or "Latest release")
    if not latest:
        raise ValueError("GitHub release did not include a version tag.")
    return AppUpdateInfo(
        current_version=__version__,
        latest_version=latest,
        release_name=release_name,
        release_url=release_url,
        update_available=is_newer_version(latest, __version__),
    )


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    length = max(len(candidate_parts), len(current_parts))
    candidate_parts.extend([0] * (length - len(candidate_parts)))
    current_parts.extend([0] * (length - len(current_parts)))
    return candidate_parts > current_parts


def _version_parts(value: str) -> list[int]:
    cleaned = value.strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    parts = [int(part) for part in re.findall(r"\d+", cleaned)]
    return parts or [0]

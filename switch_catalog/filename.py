from __future__ import annotations

import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".nsp", ".xci", ".nsz"}

REGION_WORDS = {
    "usa",
    "us",
    "eur",
    "europe",
    "japan",
    "jp",
    "asia",
    "world",
    "global",
}

SCENE_WORDS = {
    "nsw",
    "eshop",
    "rev",
    "repack",
    "proper",
    "multi",
    "dlc",
}

VERSION_RE = re.compile(r"\b(?:update\s*)?v?(\d+(?:\.\d+){1,3}|\d{4,})\b", re.IGNORECASE)
BRACKET_VERSION_RE = re.compile(r"[\[\(]v(\d+(?:\.\d+){0,3})[\]\)]", re.IGNORECASE)
UPDATE_VERSION_RE = re.compile(r"\bupdate\s+v?(\d+(?:\.\d+){0,3})\b", re.IGNORECASE)
BRACKET_RE = re.compile(r"[\[\(][^\]\)]*[\]\)]")
TITLE_ID_RE = re.compile(r"\b0100[0-9a-f]{12}\b", re.IGNORECASE)


def is_supported_game_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def detect_version(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ")
    match = BRACKET_VERSION_RE.search(stem)
    if match:
        return match.group(1)
    match = UPDATE_VERSION_RE.search(stem)
    if match:
        return match.group(1)
    match = VERSION_RE.search(stem)
    return match.group(1) if match else ""


def extract_title_id(filename: str) -> str:
    match = TITLE_ID_RE.search(filename)
    return match.group(0).upper() if match else ""


def title_id_family(filename: str) -> str:
    title_id = extract_title_id(filename)
    return title_id[:12] if title_id else ""


def is_update_or_dlc_filename(filename: str) -> bool:
    lowered = Path(filename).stem.lower()
    if re.search(r"\b(?:update|dlc)\b", lowered):
        return True
    title_id = extract_title_id(filename)
    if not title_id:
        return False
    try:
        value = int(title_id, 16)
    except ValueError:
        return False
    return (value & 0xFFF) != 0


def clean_title(filename: str, *, for_update: bool = False) -> str:
    name = Path(filename).stem
    name = VERSION_RE.sub(" ", name)
    name = name.replace(".", " ").replace("_", " ").replace("-", " ")
    name = TITLE_ID_RE.sub(" ", name)
    name = BRACKET_RE.sub(" ", name)
    name = re.sub(r"\b\d{4,}\b", " ", name)
    if for_update:
        name = re.sub(r"\bupdate\b", " ", name, flags=re.IGNORECASE)
    else:
        name = re.sub(r"\b(?:update|dlc)\b.*$", " ", name, flags=re.IGNORECASE)

    words = []
    for raw_word in re.split(r"\s+", name):
        word = raw_word.strip()
        if not word:
            continue
        lowered = word.lower()
        if lowered in REGION_WORDS or lowered in SCENE_WORDS:
            continue
        words.append(word)

    cleaned = " ".join(words)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title() if cleaned.islower() else cleaned

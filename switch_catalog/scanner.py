from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    class fuzz:  # type: ignore
        @staticmethod
        def ratio(a: str, b: str) -> float:
            return SequenceMatcher(None, a, b).ratio() * 100

from .filename import clean_title, detect_version, is_supported_game_file, title_id_family


@dataclass
class ScanSummary:
    base_files: int = 0
    update_files: int = 0
    matched_updates: int = 0
    unmatched_updates: int = 0


def iter_game_files(folder: str, recursive: bool = True, exclude_roots: list[str] | None = None):
    if not folder:
        return
    root = Path(folder)
    if not root.exists():
        return
    excludes = []
    for exclude in exclude_roots or []:
        if exclude:
            try:
                excludes.append(Path(exclude).resolve())
            except OSError:
                pass
    paths = root.rglob("*") if recursive else root.glob("*")
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if any(resolved == exclude or exclude in resolved.parents for exclude in excludes):
            continue
        if is_supported_game_file(path):
            yield path


def scan_library(
    conn: sqlite3.Connection,
    base_folder: str,
    updates_folder: str,
    *,
    recursive: bool = True,
    threshold: float = 0.82,
) -> ScanSummary:
    summary = ScanSummary()
    known_update_paths = _known_update_paths(conn)
    for path in iter_game_files(base_folder, recursive, exclude_roots=[updates_folder]):
        if str(path) in known_update_paths:
            continue
        summary.base_files += 1
        _upsert_base_game(conn, path)

    games = conn.execute(
        """
        SELECT g.id, g.display_title, g.cleaned_title, gf.file_name
        FROM games g
        LEFT JOIN game_files gf ON gf.game_id=g.id AND gf.is_base_game=1
        """
    ).fetchall()
    for path in iter_game_files(updates_folder, recursive):
        summary.update_files += 1
        matched_game_id, confidence = _match_update(path.name, games)
        if confidence < threshold:
            matched_game_id = None
        if matched_game_id:
            summary.matched_updates += 1
        else:
            summary.unmatched_updates += 1
        _upsert_update(conn, path, matched_game_id, confidence)
    conn.commit()
    return summary


def _known_update_paths(conn: sqlite3.Connection) -> set[str]:
    return {row["file_path"] for row in conn.execute("SELECT file_path FROM updates")}


def _upsert_base_game(conn: sqlite3.Connection, path: Path) -> int:
    stat = path.stat()
    cleaned = clean_title(path.name)
    display = cleaned or path.stem
    conn.execute(
        """
        INSERT INTO games(display_title, cleaned_title, last_scanned)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cleaned_title) DO UPDATE SET
            last_scanned=CURRENT_TIMESTAMP
        """,
        (display, cleaned or display),
    )
    game_id = conn.execute("SELECT id FROM games WHERE cleaned_title=?", (cleaned or display,)).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO game_files(game_id, file_path, file_name, file_extension, file_size, modified_time, file_type, is_base_game)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(file_path) DO UPDATE SET
            game_id=excluded.game_id,
            file_name=excluded.file_name,
            file_extension=excluded.file_extension,
            file_size=excluded.file_size,
            modified_time=excluded.modified_time,
            file_type=excluded.file_type
        """,
        (game_id, str(path), path.name, path.suffix.lower(), stat.st_size, stat.st_mtime, path.suffix[1:].upper()),
    )
    return game_id


def _upsert_update(conn: sqlite3.Connection, path: Path, game_id: int | None, confidence: float) -> None:
    stat = path.stat()
    conn.execute(
        """
        INSERT INTO updates(game_id, file_path, file_name, detected_version, file_size, modified_time, match_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            game_id=CASE WHEN updates.manual_match=1 THEN updates.game_id ELSE excluded.game_id END,
            file_name=excluded.file_name,
            detected_version=excluded.detected_version,
            file_size=excluded.file_size,
            modified_time=excluded.modified_time,
            match_confidence=CASE WHEN updates.manual_match=1 THEN updates.match_confidence ELSE excluded.match_confidence END
        """,
        (game_id, str(path), path.name, detect_version(path.name), stat.st_size, stat.st_mtime, confidence),
    )


def _match_update(filename: str, games) -> tuple[int | None, float]:
    id_match = _match_update_by_title_id(filename, games)
    if id_match[0] is not None:
        return id_match
    update_title = clean_title(filename, for_update=True).lower()
    if not update_title:
        return None, 0.0
    best_id = None
    best_score = 0.0
    for game in games:
        candidates = {game["cleaned_title"].lower(), game["display_title"].lower()}
        score = max(_title_match_score(update_title, candidate) for candidate in candidates)
        if score > best_score:
            best_score = score
            best_id = int(game["id"])
    return best_id, best_score


def _match_update_by_title_id(filename: str, games) -> tuple[int | None, float]:
    update_family = title_id_family(filename)
    if not update_family:
        return None, 0.0
    matches = []
    for game in games:
        base_family = title_id_family(game["file_name"] or "")
        if base_family and base_family == update_family:
            matches.append(int(game["id"]))
    if len(set(matches)) == 1:
        return matches[0], 0.99
    return None, 0.0


def _title_match_score(update_title: str, game_title: str) -> float:
    update_title = " ".join(update_title.split())
    game_title = " ".join(game_title.split())
    if not update_title or not game_title:
        return 0.0
    if update_title == game_title:
        return 1.0
    if update_title.startswith(game_title + " "):
        return 0.96
    if update_title.startswith(game_title):
        return 0.93
    if f" {game_title} " in f" {update_title} ":
        return 0.9
    if update_title.startswith(f"{game_title} {game_title} "):
        return 0.97
    return float(fuzz.ratio(update_title, game_title)) / 100

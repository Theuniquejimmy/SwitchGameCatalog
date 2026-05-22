from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .paths import DB_PATH, ensure_app_dirs


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_app_dirs()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_title TEXT NOT NULL,
            cleaned_title TEXT NOT NULL UNIQUE,
            metadata_provider TEXT,
            metadata_provider_id TEXT,
            description TEXT,
            release_date TEXT,
            developer TEXT,
            publisher TEXT,
            genres TEXT,
            cover_image_path TEXT,
            cover_image_url TEXT,
            date_added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_scanned TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata_locked INTEGER NOT NULL DEFAULT 0,
            needs_review INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS game_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            file_extension TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            modified_time REAL NOT NULL,
            file_type TEXT NOT NULL,
            is_base_game INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            detected_version TEXT,
            file_size INTEGER NOT NULL,
            modified_time REAL NOT NULL,
            match_confidence REAL NOT NULL DEFAULT 0,
            manual_match INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS screenshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            image_url TEXT NOT NULL,
            local_path TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(game_id, image_url)
        );

        CREATE TABLE IF NOT EXISTS metadata_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            query TEXT NOT NULL,
            response_json TEXT NOT NULL,
            cached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(provider, query)
        );
        """
    )
    _ensure_column(conn, "updates", "manual_match", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "games", "favorite", "INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def reset_library_cache(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM updates")
    conn.execute("DELETE FROM screenshots")
    conn.execute("DELETE FROM game_files")
    conn.execute("DELETE FROM games")
    conn.execute(
        "DELETE FROM sqlite_sequence WHERE name IN ('updates', 'screenshots', 'game_files', 'games')"
    )
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if data.get("genres"):
        try:
            data["genres"] = json.loads(data["genres"])
        except json.JSONDecodeError:
            data["genres"] = []
    return data


def upsert_cache(conn: sqlite3.Connection, provider: str, query: str, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO metadata_cache(provider, query, response_json, cached_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(provider, query) DO UPDATE SET
            response_json=excluded.response_json,
            cached_at=CURRENT_TIMESTAMP
        """,
        (provider, query, json.dumps(payload)),
    )
    conn.commit()


def get_cache(conn: sqlite3.Connection, provider: str, query: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT response_json FROM metadata_cache WHERE provider=? AND query=?",
        (provider, query),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["response_json"])
    except json.JSONDecodeError:
        return None

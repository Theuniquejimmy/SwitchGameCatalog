from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import requests

from .db import get_cache, upsert_cache

MIN_AUTO_MATCH_CONFIDENCE = 0.86
IGDB_SEARCH_CACHE_VERSION = "v2"


@dataclass
class MetadataResult:
    provider: str
    provider_id: str
    title: str
    description: str = ""
    release_date: str = ""
    developer: str = ""
    publisher: str = ""
    genres: list[str] | None = None
    cover_image_url: str = ""
    trailer_url: str = ""
    screenshots: list[str] | None = None
    confidence: float = 0.0


class IgdbProvider:
    name = "igdb"

    def __init__(self, client_id: str = "", client_secret: str = "") -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()

    def search(self, conn: sqlite3.Connection, title: str) -> list[MetadataResult]:
        queries = metadata_search_queries(title)
        if not queries or not self.client_id or not self.client_secret:
            return []
        results = []
        seen = set()
        for query in queries:
            results.extend(self._search_one(conn, query, title, restrict_to_switch=True, seen=seen))
        if not results:
            for query in queries:
                results.extend(self._search_one(conn, query, title, restrict_to_switch=False, seen=seen))
        return sorted(results, key=lambda item: item.confidence, reverse=True)

    def _search_one(
        self,
        conn: sqlite3.Connection,
        query: str,
        original_title: str,
        *,
        restrict_to_switch: bool,
        seen: set[str],
    ) -> list[MetadataResult]:
        cache_key = f"search:{IGDB_SEARCH_CACHE_VERSION}:{'switch' if restrict_to_switch else 'all'}:{query}"
        cached = get_cache(conn, self.name, cache_key)
        if cached is None:
            where = "where platforms = (130); " if restrict_to_switch else ""
            body = (
                'search "{query}"; '
                "fields name,summary,first_release_date,"
                "cover.url,screenshots.url,genres.name,videos.video_id,"
                "involved_companies.developer,involved_companies.publisher,involved_companies.company.name; "
                "{where}limit 10;"
            ).format(query=_escape_igdb_query(query), where=where)
            response = requests.post(
                "https://api.igdb.com/v4/games",
                headers=self._headers(conn),
                data=body.encode("utf-8"),
                timeout=20,
            )
            response.raise_for_status()
            cached = {"results": response.json()}
            upsert_cache(conn, self.name, cache_key, cached)
        results = []
        for item in cached.get("results", []):
            provider_id = str(item.get("id", ""))
            if provider_id in seen:
                continue
            seen.add(provider_id)
            results.append(self._from_raw(item, original_title))
        return results

    def enrich(self, conn: sqlite3.Connection, result: MetadataResult) -> MetadataResult:
        return result

    def _from_raw(self, item: dict[str, Any], query: str) -> MetadataResult:
        developers = []
        publishers = []
        for company in item.get("involved_companies", []) or []:
            name = (company.get("company") or {}).get("name", "")
            if company.get("developer") and name:
                developers.append(name)
            if company.get("publisher") and name:
                publishers.append(name)
        screenshots = [
            _igdb_image_url(shot.get("url", ""), "t_1080p")
            for shot in item.get("screenshots", []) or []
            if shot.get("url")
        ]
        release_date = ""
        if item.get("first_release_date"):
            release_date = datetime.fromtimestamp(
                int(item["first_release_date"]), tz=timezone.utc
            ).date().isoformat()
        name = item.get("name") or query
        return MetadataResult(
            provider=self.name,
            provider_id=str(item.get("id", "")),
            title=name,
            description=item.get("summary") or "",
            release_date=release_date,
            developer=", ".join(developers),
            publisher=", ".join(publishers),
            genres=[genre.get("name", "") for genre in item.get("genres", []) or [] if genre.get("name")],
            cover_image_url=_igdb_image_url((item.get("cover") or {}).get("url", ""), "t_cover_big_2x"),
            trailer_url=_igdb_trailer_url(item.get("videos", []) or []),
            screenshots=screenshots,
            confidence=_title_similarity(name, query),
        )

    def _headers(self, conn: sqlite3.Connection) -> dict[str, str]:
        token = self._access_token(conn)
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _access_token(self, conn: sqlite3.Connection) -> str:
        cache_key = f"token:{self.client_id}"
        cached = get_cache(conn, self.name, cache_key)
        if cached and cached.get("access_token"):
            return str(cached["access_token"])
        response = requests.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        response.raise_for_status()
        cached = response.json()
        upsert_cache(conn, self.name, cache_key, cached)
        return str(cached["access_token"])


def _igdb_image_url(url: str, size: str) -> str:
    if not url:
        return ""
    url = url.replace("t_thumb", size)
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url


def _igdb_trailer_url(videos: list[dict[str, Any]]) -> str:
    for video in videos:
        video_id = str(video.get("video_id") or "").strip()
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def metadata_search_queries(title: str) -> list[str]:
    normalized = normalize_metadata_title(title)
    candidates = [normalized]
    if ":" in normalized:
        head, tail = [part.strip() for part in normalized.split(":", 1)]
        if head and tail:
            candidates.append(f"{head} {tail}")
        if head:
            candidates.append(head)
    candidates.append(re.sub(r"\b(the|a|an)\b", " ", normalized, flags=re.IGNORECASE))
    deduped = []
    seen = set()
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip(" -:")
        key = candidate.lower()
        if candidate and key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def normalize_metadata_title(title: str) -> str:
    value = title.strip()
    replacements = {
        "\ua789": ":",
        "\uff1a": ":",
        "\u2013": "-",
        "\u2014": "-",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2122": "",
        "\u00ae": "",
        "\u00a9": "",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = re.sub(r"\[[^\]]+\]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _title_similarity(left: str, right: str) -> float:
    left_norm = normalize_metadata_title(left).lower()
    right_norm = normalize_metadata_title(right).lower()
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm.startswith(right_norm) or right_norm.startswith(left_norm):
        return 0.9
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _escape_igdb_query(query: str) -> str:
    return query.replace("\\", "\\\\").replace('"', '\\"')


def provider_from_settings(
    provider: str,
    *,
    igdb_client_id: str = "",
    igdb_client_secret: str = "",
):
    return IgdbProvider(igdb_client_id, igdb_client_secret)


def fetch_and_apply_metadata(
    conn: sqlite3.Connection,
    game_id: int,
    title: str,
    *,
    provider: str,
    igdb_client_id: str = "",
    igdb_client_secret: str = "",
    force: bool = False,
) -> bool:
    row = conn.execute("SELECT metadata_locked FROM games WHERE id=?", (game_id,)).fetchone()
    if row and row["metadata_locked"] and not force:
        return False
    provider_impl = provider_from_settings(
        provider,
        igdb_client_id=igdb_client_id,
        igdb_client_secret=igdb_client_secret,
    )
    results = provider_impl.search(conn, title)
    if not results:
        conn.execute("UPDATE games SET needs_review=1 WHERE id=?", (game_id,))
        conn.commit()
        return False
    best = provider_impl.enrich(conn, sorted(results, key=lambda item: item.confidence, reverse=True)[0])
    if best.confidence < MIN_AUTO_MATCH_CONFIDENCE:
        conn.execute("UPDATE games SET needs_review=1 WHERE id=?", (game_id,))
        conn.commit()
        return False
    apply_metadata_result(conn, game_id, best, needs_review=False)
    return True


def apply_metadata_result(
    conn: sqlite3.Connection,
    game_id: int,
    result: MetadataResult,
    *,
    needs_review: bool = False,
    lock: bool = False,
) -> None:
    conn.execute(
        """
        UPDATE games SET
            display_title=?,
            metadata_provider=?,
            metadata_provider_id=?,
            description=?,
            release_date=?,
            developer=?,
            publisher=?,
            genres=?,
            cover_image_url=?,
            trailer_url=?,
            needs_review=?,
            metadata_locked=CASE WHEN ? THEN 1 ELSE metadata_locked END
        WHERE id=?
        """,
        (
            result.title,
            result.provider,
            result.provider_id,
            result.description,
            result.release_date,
            result.developer,
            result.publisher,
            json.dumps(result.genres or []),
            result.cover_image_url,
            result.trailer_url,
            1 if needs_review else 0,
            1 if lock else 0,
            game_id,
        ),
    )
    conn.execute("DELETE FROM screenshots WHERE game_id=?", (game_id,))
    for index, url in enumerate(result.screenshots or []):
        conn.execute(
            "INSERT OR IGNORE INTO screenshots(game_id, image_url, sort_order) VALUES (?, ?, ?)",
            (game_id, url, index),
        )
    conn.commit()


def fetch_missing_metadata(
    conn: sqlite3.Connection,
    *,
    provider: str,
    igdb_client_id: str = "",
    igdb_client_secret: str = "",
    force: bool = False,
    limit: int | None = None,
) -> tuple[int, int]:
    query = "SELECT id, display_title, cleaned_title FROM games WHERE metadata_locked=0"
    if not force:
        query += " AND (metadata_provider IS NULL OR description IS NULL OR description='' OR trailer_url IS NULL)"
    query += " ORDER BY display_title COLLATE NOCASE"
    if limit:
        query += f" LIMIT {int(limit)}"
    attempted = 0
    updated = 0
    for row in conn.execute(query).fetchall():
        attempted += 1
        if fetch_and_apply_metadata(
            conn,
            int(row["id"]),
            row["cleaned_title"] or row["display_title"],
            provider=provider,
            igdb_client_id=igdb_client_id,
            igdb_client_secret=igdb_client_secret,
            force=force,
        ):
            updated += 1
    return attempted, updated


def _strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()

import sqlite3

from switch_catalog.db import init_db, upsert_cache
from switch_catalog.metadata import (
    IgdbProvider,
    MetadataResult,
    apply_metadata_result,
    metadata_search_queries,
    normalize_metadata_title,
    _title_similarity,
)


def test_normalizes_modifier_colon_for_metadata_search():
    assert normalize_metadata_title("Bloomtown꞉ A Different Story") == "Bloomtown: A Different Story"


def test_metadata_queries_include_colonless_variant():
    queries = metadata_search_queries("Bloomtown꞉ A Different Story")
    assert "Bloomtown: A Different Story" in queries
    assert "Bloomtown A Different Story" in queries
    assert "Bloomtown" in queries


def test_bad_metadata_match_scores_low():
    assert _title_similarity("A Musical Story", "Bloomtown: A Different Story") < 0.5


def test_igdb_metadata_extracts_youtube_trailer_url():
    result = IgdbProvider()._from_raw(
        {"id": 1, "name": "Example Game", "videos": [{"video_id": "abc123"}]},
        "Example Game",
    )

    assert result.trailer_url == "https://www.youtube.com/watch?v=abc123"


def test_igdb_search_ignores_old_trailerless_cache(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    upsert_cache(
        conn,
        "igdb",
        "search:switch:Example Game",
        {"results": [{"id": 1, "name": "Example Game"}]},
    )
    provider = IgdbProvider("client", "secret")
    monkeypatch.setattr(provider, "_headers", lambda conn: {})

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return [{"id": 1, "name": "Example Game", "videos": [{"video_id": "abc123"}]}]

    monkeypatch.setattr("switch_catalog.metadata.requests.post", lambda *args, **kwargs: FakeResponse())

    results = provider.search(conn, "Example Game")

    assert results[0].trailer_url == "https://www.youtube.com/watch?v=abc123"


def test_apply_metadata_result_stores_trailer_url():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    game_id = conn.execute(
        "INSERT INTO games(display_title, cleaned_title) VALUES (?, ?)",
        ("Example Game", "Example Game"),
    ).lastrowid

    apply_metadata_result(
        conn,
        int(game_id),
        MetadataResult(
            provider="igdb",
            provider_id="1",
            title="Example Game",
            trailer_url="https://www.youtube.com/watch?v=abc123",
        ),
    )

    row = conn.execute("SELECT trailer_url FROM games WHERE id=?", (game_id,)).fetchone()
    assert row["trailer_url"] == "https://www.youtube.com/watch?v=abc123"

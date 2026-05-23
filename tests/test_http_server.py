from __future__ import annotations

import base64
import sqlite3
import urllib.error
import urllib.request

from switch_catalog.db import init_db
from switch_catalog.http_server import CatalogHttpServer
from switch_catalog.settings import AppSettings


def _catalog_db(tmp_path):
    db_path = tmp_path / "library.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    game = tmp_path / "Game One.nsp"
    update = tmp_path / "Game One Update.nsp"
    game.write_bytes(b"0123456789")
    update.write_bytes(b"update")
    conn.execute(
        "INSERT INTO games(display_title, cleaned_title) VALUES (?, ?)",
        ("Game One", "Game One"),
    )
    game_id = conn.execute("SELECT id FROM games").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO game_files(game_id, file_path, file_name, file_extension, file_size, modified_time, file_type, is_base_game)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (game_id, str(game), game.name, ".nsp", game.stat().st_size, game.stat().st_mtime, "NSP"),
    )
    conn.execute(
        """
        INSERT INTO updates(game_id, file_path, file_name, file_size, modified_time)
        VALUES (?, ?, ?, ?, ?)
        """,
        (game_id, str(update), update.name, update.stat().st_size, update.stat().st_mtime),
    )
    conn.commit()
    conn.close()
    return db_path


def _start_server(db_path, **settings):
    server = CatalogHttpServer(db_path)
    server.start(AppSettings(http_server_port=0, **settings))
    return server


def _read(url: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


def test_directory_listing_exposes_catalog_names_only(tmp_path):
    server = _start_server(_catalog_db(tmp_path))
    try:
        status, headers, body = _read(f"http://127.0.0.1:{server.port}/dir/")
    finally:
        server.stop()

    assert status == 200
    assert headers["Accept-Ranges"] == "bytes"
    assert b"Game One.nsp" in body
    assert b"Game One Update.nsp" in body


def test_download_by_id_and_range_request(tmp_path):
    server = _start_server(_catalog_db(tmp_path))
    try:
        status, headers, body = _read(
            f"http://127.0.0.1:{server.port}/dl/game/1/Game%20One.nsp",
            {"Range": "bytes=2-5"},
        )
    finally:
        server.stop()

    assert status == 206
    assert headers["Content-Range"] == "bytes 2-5/10"
    assert body == b"2345"


def test_list_txt_embeds_credentials_when_password_is_set(tmp_path):
    server = _start_server(
        _catalog_db(tmp_path),
        http_server_username="switch",
        http_server_password="secret",
    )
    auth = base64.b64encode(b"switch:secret").decode("ascii")
    try:
        with pytest_raises_401(f"http://127.0.0.1:{server.port}/list.txt"):
            pass
        status, _headers, body = _read(
            f"http://127.0.0.1:{server.port}/list.txt",
            {"Authorization": f"Basic {auth}"},
        )
    finally:
        server.stop()

    assert status == 200
    assert b"http://switch:secret@127.0.0.1:" in body
    assert b"/dl/game/1/Game%20One.nsp" in body


class pytest_raises_401:
    def __init__(self, url: str) -> None:
        self.url = url

    def __enter__(self):
        try:
            urllib.request.urlopen(self.url, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
            return exc
        raise AssertionError("Expected HTTP 401")

    def __exit__(self, exc_type, exc, tb):
        return False

import sqlite3

from switch_catalog.db import init_db
from switch_catalog.scanner import _match_update_by_title_id, _title_match_score, scan_library


def test_dlc_filename_prefix_matches_base_game():
    assert _title_match_score("animal crossing new horizons happy home paradise", "animal crossing new horizons") >= 0.9


def test_short_dlc_title_matches_base_game():
    assert _title_match_score("blasphemous the golden burden", "blasphemous") >= 0.9


def test_title_id_family_matches_update_to_base():
    games = [
        {
            "id": 7,
            "display_title": "Bloomtown: A Different Story",
            "cleaned_title": "Bloomtown: A Different Story",
            "file_name": "Bloomtown [0100AF401C8E4000][v0].nsp",
        }
    ]
    assert _match_update_by_title_id("Bloomtown [0100AF401C8E4800][v6].nsp", games) == (7, 0.99)


def test_scan_does_not_readd_file_already_marked_as_update(tmp_path):
    base = tmp_path / "base"
    updates = tmp_path / "updates"
    base.mkdir()
    updates.mkdir()
    file_path = base / "Mistaken DLC [0100000000000000][v0].nsp"
    file_path.write_bytes(b"dlc")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        """
        INSERT INTO updates(game_id, file_path, file_name, detected_version, file_size, modified_time, match_confidence, manual_match)
        VALUES (NULL, ?, ?, '', 3, 0, 0, 0)
        """,
        (str(file_path), file_path.name),
    )
    conn.commit()

    summary = scan_library(conn, str(base), str(updates))

    assert summary.base_files == 0
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0


def test_scan_catalogs_nsz_files(tmp_path):
    base = tmp_path / "base"
    updates = tmp_path / "updates"
    base.mkdir()
    updates.mkdir()
    file_path = base / "Compressed Game [0100000000000000][v0].nsz"
    file_path.write_bytes(b"base")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    summary = scan_library(conn, str(base), str(updates))

    row = conn.execute("SELECT file_name, file_extension FROM game_files").fetchone()
    assert summary.base_files == 1
    assert row["file_name"] == file_path.name
    assert row["file_extension"] == ".nsz"


def test_scan_mixed_folder_keeps_nsz_base_and_matches_update(tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    base_file = folder / "Bloomtown [0100AF401C8E4000][v0].nsz"
    update_file = folder / "Bloomtown Update [0100AF401C8E4800][v6].nsp"
    base_file.write_bytes(b"base")
    update_file.write_bytes(b"update")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    summary = scan_library(conn, str(folder), str(folder))

    base_row = conn.execute("SELECT file_name, file_extension FROM game_files").fetchone()
    update_row = conn.execute("SELECT u.file_name, u.game_id FROM updates u").fetchone()
    game_id = conn.execute("SELECT id FROM games").fetchone()["id"]
    assert summary.base_files == 1
    assert summary.update_files == 1
    assert summary.matched_updates == 1
    assert base_row["file_name"] == base_file.name
    assert base_row["file_extension"] == ".nsz"
    assert update_row["file_name"] == update_file.name
    assert update_row["game_id"] == game_id

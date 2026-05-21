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


def test_scan_mixed_nsz_folder_splits_base_games_and_updates(tmp_path):
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    base_file = mixed / "Example Game [0100000000000000][v0].nsz"
    update_file = mixed / "Example Game Update [0100000000000800][v65536].nsz"
    base_file.write_bytes(b"base")
    update_file.write_bytes(b"update")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    summary = scan_library(conn, str(mixed), str(mixed))

    assert summary.base_files == 1
    assert summary.update_files == 1
    assert summary.matched_updates == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM updates WHERE game_id IS NOT NULL").fetchone()[0] == 1


def test_scan_mixed_folder_promotes_existing_base_nsz_from_updates(tmp_path):
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    base_file = mixed / "Example Game [0100000000000000][v0].nsz"
    base_file.write_bytes(b"base")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        """
        INSERT INTO updates(game_id, file_path, file_name, detected_version, file_size, modified_time, match_confidence, manual_match)
        VALUES (NULL, ?, ?, '0', 4, 0, 0, 0)
        """,
        (str(base_file), base_file.name),
    )
    conn.commit()

    summary = scan_library(conn, str(mixed), str(mixed))

    assert summary.base_files == 1
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM updates").fetchone()[0] == 0

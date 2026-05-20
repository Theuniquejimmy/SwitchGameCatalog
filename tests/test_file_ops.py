from switch_catalog.file_ops import move_file_to_folder, move_files_to_folder, unique_destination


def test_unique_destination_adds_suffix(tmp_path):
    target = tmp_path / "Game.nsp"
    target.write_text("existing", encoding="utf-8")

    assert unique_destination(tmp_path, "Game.nsp").name == "Game (1).nsp"


def test_move_file_to_folder_avoids_overwrite(tmp_path):
    source = tmp_path / "source" / "Game.nsp"
    source.parent.mkdir()
    source.write_text("new", encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir()
    (install / "Game.nsp").write_text("existing", encoding="utf-8")

    moved = move_file_to_folder(source, install)

    assert moved.name == "Game (1).nsp"
    assert moved.read_text(encoding="utf-8") == "new"
    assert not source.exists()


def test_move_file_to_same_folder_is_noop(tmp_path):
    source = tmp_path / "Game.nsp"
    source.write_text("same", encoding="utf-8")

    moved = move_file_to_folder(source, tmp_path)

    assert moved == source
    assert source.read_text(encoding="utf-8") == "same"


def test_move_files_to_folder_moves_batch(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    first = source_root / "Game.nsp"
    second = source_root / "Update.nsp"
    first.write_text("game", encoding="utf-8")
    second.write_text("update", encoding="utf-8")
    install = tmp_path / "install"

    moved = move_files_to_folder([first, second], install)

    assert [path.name for path in moved] == ["Game.nsp", "Update.nsp"]
    assert (install / "Game.nsp").read_text(encoding="utf-8") == "game"
    assert (install / "Update.nsp").read_text(encoding="utf-8") == "update"
    assert not first.exists()
    assert not second.exists()

import subprocess

from switch_catalog import file_ops
from switch_catalog.file_ops import MtpStorageInfo, move_file_to_folder, move_files_to_folder, unique_destination


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


def test_move_file_to_shell_folder_waits_for_mtp_copy_success(tmp_path, monkeypatch):
    source = tmp_path / "Game.nsp"
    source.write_text("mtp", encoding="utf-8")
    calls = []

    def fake_copy(path, folder, timeout_seconds=1800):
        calls.append((path, folder, timeout_seconds))

    monkeypatch.setattr(file_ops, "_copy_to_shell_folder", fake_copy)

    moved = move_file_to_folder(source, "shell:::{device}")

    assert moved == "shell:::{device}\\Game.nsp"
    assert calls == [(source, "shell:::{device}", 1800)]
    assert source.exists()


def test_move_files_to_shell_folder_uses_one_ordered_batch(tmp_path, monkeypatch):
    base = tmp_path / "Base.nsp"
    update = tmp_path / "Update.nsp"
    dlc = tmp_path / "DLC.nsp"
    for source in (base, update, dlc):
        source.write_text("mtp", encoding="utf-8")
    calls = []

    def fake_copy(paths, folder, timeout_seconds=1800):
        calls.append((paths, folder, timeout_seconds))

    monkeypatch.setattr(file_ops, "_copy_files_to_shell_folder", fake_copy)

    moved = move_files_to_folder([base, update, dlc], "shell:::{device}")

    assert moved == [
        "shell:::{device}\\Base.nsp",
        "shell:::{device}\\Update.nsp",
        "shell:::{device}\\DLC.nsp",
    ]
    assert calls == [([base, update, dlc], "shell:::{device}", 1800)]
    assert base.exists()
    assert update.exists()
    assert dlc.exists()


def test_copy_files_to_shell_folder_uses_single_file_helper_in_order(tmp_path, monkeypatch):
    base = tmp_path / "Base.nsp"
    update = tmp_path / "Update.nsp"
    dlc = tmp_path / "DLC.nsp"
    for source in (base, update, dlc):
        source.write_text("mtp", encoding="utf-8")
    calls = []
    sleeps = []

    def fake_copy(path, folder, timeout_seconds=1800):
        calls.append((path, folder, timeout_seconds))

    monkeypatch.setattr(file_ops, "_copy_to_shell_folder", fake_copy)
    monkeypatch.setattr(file_ops.time, "sleep", lambda seconds: sleeps.append(seconds))

    file_ops._copy_files_to_shell_folder([base, update, dlc], "shell:::{device}", timeout_seconds=7)

    assert calls == [
        (base, "shell:::{device}", 7),
        (update, "shell:::{device}", 7),
        (dlc, "shell:::{device}", 7),
    ]
    assert sleeps == [1, 1]
    assert base.exists()
    assert update.exists()
    assert dlc.exists()


def test_copy_to_shell_folder_waits_for_windows_file_operation_window(tmp_path, monkeypatch):
    source = tmp_path / "Game.nsp"
    source.write_text("mtp", encoding="utf-8")
    seen_script = ""

    def fake_run(*args, **kwargs):
        nonlocal seen_script
        command = args[0]
        encoded = command[command.index("-EncodedCommand") + 1]
        seen_script = file_ops.base64.b64decode(encoded).decode("utf-16le")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(file_ops.subprocess, "run", fake_run)

    file_ops._copy_to_shell_folder(source, "shell:::{device}", timeout_seconds=5)

    assert "Wait-ForShellFileOperation $timeoutSeconds" in seen_script
    assert "MessageBox" not in seen_script
    assert source.exists()


def test_copy_to_shell_folder_raises_on_powershell_failure(tmp_path, monkeypatch):
    source = tmp_path / "Game.nsp"
    source.write_text("mtp", encoding="utf-8")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="device disconnected")

    monkeypatch.setattr(file_ops.subprocess, "run", fake_run)

    try:
        file_ops._copy_to_shell_folder(source, "shell:::{device}", timeout_seconds=5)
    except RuntimeError as exc:
        assert str(exc) == "MTP transfer failed for 'Game.nsp': device disconnected"
    else:
        raise AssertionError("Expected RuntimeError")


def test_copy_to_shell_folder_raises_on_timeout(tmp_path, monkeypatch):
    source = tmp_path / "Game.nsp"
    source.write_text("mtp", encoding="utf-8")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(file_ops.subprocess, "run", fake_run)

    try:
        file_ops._copy_to_shell_folder(source, "shell:::{device}", timeout_seconds=5)
    except TimeoutError as exc:
        assert str(exc) == "MTP transfer timed out for 'Game.nsp' after 5 seconds."
    else:
        raise AssertionError("Expected TimeoutError")


def test_mtp_storage_status_formats_nand_then_sd():
    rows = [
        MtpStorageInfo("SD install", 64 * 1024**3, 128 * 1024**3),
        MtpStorageInfo("NAND install", 8 * 1024**3, 32 * 1024**3),
    ]

    assert file_ops._format_mtp_storage_status(rows) == (
        "NAND install: 8.0 GB free / 32.0 GB | SD install: 64.0 GB free / 128.0 GB"
    )


def test_mtp_destination_storage_info_matches_shell_prefixed_child_path(monkeypatch):
    rows = [
        MtpStorageInfo("SD install", 64, 128, "::{device}\\SID-{10005,,128}"),
        MtpStorageInfo("NAND install", 8, 32, "::{device}\\SID-{10006,,32}"),
    ]
    monkeypatch.setattr(file_ops, "list_mtp_storage_info", lambda timeout_seconds=8: rows)

    storage = file_ops.mtp_destination_storage_info("shell:::{device}\\SID-{10005,,128}\\Nintendo")

    assert storage == rows[0]


def test_mtp_install_destination_info_finds_nand_and_sd_targets(monkeypatch):
    rows = [
        MtpStorageInfo("SD install", 64, 128, "::{device}\\SID-{10005,,128}"),
        MtpStorageInfo("NAND install", 8, 32, "::{device}\\SID-{10006,,32}"),
    ]
    monkeypatch.setattr(file_ops, "list_mtp_storage_info", lambda timeout_seconds=8: rows)

    assert file_ops.mtp_install_destination_info("sd") == rows[0]
    assert file_ops.mtp_install_destination_info("SD Card Install") == rows[0]
    assert file_ops.mtp_install_destination_info("nand") == rows[1]
    assert file_ops.mtp_install_destination_label("sd") == "SD Card Install"
    assert file_ops.mtp_install_destination_label("nand") == "NAND Install"

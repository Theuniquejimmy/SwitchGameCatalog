import json

from switch_catalog import settings as settings_mod
from switch_catalog.settings import load_settings, normalize_folder
from switch_catalog.ui import (
    MainWindow,
    _detected_version_suffix,
    _display_folder,
    _install_size_text,
    _path_is_install_destination,
    _run_mtp_install_queue,
    _update_file_group,
    _youtube_embed_url,
    _youtube_player_url,
)


def test_normalize_folder_preserves_shell_mtp_paths():
    assert normalize_folder("shell:::{device}") == "shell:::{device}"


def test_normalize_folder_adds_shell_prefix_to_parsing_paths():
    assert normalize_folder("::{device}") == "shell:::{device}"


def test_load_settings_infers_legacy_mtp_install_destination(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "install_folder": "shell:::{device}\\SID-{10005,,128}",
                "install_folder_label": "Nintendo Switch/SD install",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)

    settings = load_settings()

    assert settings.install_destination == "sd"


def test_display_folder_uses_label_for_shell_mtp_paths():
    assert _display_folder("shell:::{device}\\SID-{10005,,1}", "Switch/Install") == "Switch/Install"


def test_display_folder_keeps_local_paths():
    assert _display_folder("C:\\Games", "Switch/Install") == "C:\\Games"


def test_path_is_install_destination_handles_local_and_shell_paths():
    assert _path_is_install_destination("C:\\Games\\Install\\Game.nsp", "C:\\Games\\Install")
    assert _path_is_install_destination("shell:::{device}\\Install\\Game.nsp", "shell:::{device}\\Install")
    assert not _path_is_install_destination("C:\\Games\\Library\\Game.nsp", "C:\\Games\\Install")


def test_update_file_group_splits_updates_and_dlc():
    assert _update_file_group("Game Update [0100000000000800][v65536].nsp") == "Updates"
    assert _update_file_group("Game DLC Pack [0100000000000001][v0].nsp") == "DLC"


def test_detected_version_suffix_shows_raw_and_dotted_versions():
    assert _detected_version_suffix("131072") == " (v131072) (v2.0)"
    assert _detected_version_suffix("2.0") == " (v131072) (v2.0)"
    assert _detected_version_suffix("3.0.1") == " (v196609) (v3.0.1)"


def test_install_size_text_includes_selected_update_and_dlc_size():
    assert _install_size_text(1024**3, 3, 512 * 1024**2) == (
        "Install size: Base 1.0 GB + 3 update/DLC file(s) 512.0 MB = Total size 1.5 GB"
    )


def test_process_install_items_reports_failures_and_continues():
    class Settings:
        install_folder = "C:\\Install"

    class WindowStub:
        settings = Settings()

        def process_install_item(self, item):
            return item.get("error")

    moved, failures = MainWindow.process_install_items(
        WindowStub(),
        [
            {"file_name": "Base.nsp"},
            {"file_name": "Update.nsp", "error": "Update.nsp: transfer failed"},
            {"file_name": "DLC.nsp"},
        ],
    )

    assert moved == 2
    assert failures == ["Update.nsp: transfer failed"]


def test_process_install_items_uses_one_mtp_batch_for_shell_destinations():
    class Settings:
        install_folder = "shell:::{device}"

    class WindowStub:
        settings = Settings()
        batch_items = None

        def process_mtp_install_batch(self, items):
            self.batch_items = items
            return len(items), []

        def process_install_item(self, item):
            raise AssertionError("MTP installs should use the batch path")

    window = WindowStub()
    items = [{"file_name": "Base.nsp"}, {"file_name": "Update.nsp"}]

    moved, failures = MainWindow.process_install_items(window, items)

    assert moved == 2
    assert failures == []
    assert window.batch_items == items


def test_run_mtp_install_queue_stops_after_first_failure(monkeypatch):
    calls = []

    def fake_move(source, folder):
        calls.append((source, folder))
        if source == "Update.nsp":
            raise RuntimeError("Switch rejected file")
        return f"{folder}\\{source}"

    monkeypatch.setattr("switch_catalog.ui.move_file_to_folder", fake_move)
    monkeypatch.setattr("switch_catalog.ui.time.sleep", lambda seconds: None)

    results = _run_mtp_install_queue(["Base.nsp", "Update.nsp", "DLC.nsp"], "shell:::{device}")

    assert results == [
        ("shell:::{device}\\Base.nsp", None),
        ("", "Switch rejected file"),
    ]
    assert calls == [("Base.nsp", "shell:::{device}"), ("Update.nsp", "shell:::{device}")]


def test_youtube_embed_url_converts_watch_and_short_urls():
    assert _youtube_embed_url("https://www.youtube.com/watch?v=abc123") == "https://www.youtube.com/embed/abc123"
    assert _youtube_embed_url("https://youtu.be/abc123") == "https://www.youtube.com/embed/abc123"


def test_youtube_player_url_uses_watch_page_for_webengine():
    assert _youtube_player_url("https://www.youtube.com/watch?v=abc123") == "https://www.youtube.com/watch?v=abc123"
    assert _youtube_player_url("https://youtu.be/abc123") == "https://www.youtube.com/watch?v=abc123"
    assert _youtube_player_url("https://www.youtube.com/embed/abc123") == "https://www.youtube.com/watch?v=abc123"

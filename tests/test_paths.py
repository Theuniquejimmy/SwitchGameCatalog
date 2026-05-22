from switch_catalog import paths


def test_ensure_app_dirs_moves_legacy_cache_to_local_appdata(tmp_path, monkeypatch):
    legacy = tmp_path / ".switch_library_catalog"
    new_app_dir = tmp_path / "LocalAppData" / "Switch Game Catalog"
    legacy.mkdir()
    (legacy / "library.sqlite3").write_text("db", encoding="utf-8")
    (legacy / "settings.json").write_text("{}", encoding="utf-8")
    (legacy / "versions.json").write_text("{}", encoding="utf-8")
    (legacy / "versions.txt").write_text("id|rightsId|version\n", encoding="utf-8")
    images = legacy / "images"
    images.mkdir()
    (images / "cover.jpg").write_bytes(b"cover")

    monkeypatch.setattr(paths, "LEGACY_APP_DIR", legacy)
    monkeypatch.setattr(paths, "APP_DIR", new_app_dir)
    monkeypatch.setattr(paths, "DB_PATH", new_app_dir / "library.sqlite3")
    monkeypatch.setattr(paths, "SETTINGS_PATH", new_app_dir / "settings.json")
    monkeypatch.setattr(paths, "IMAGE_CACHE_DIR", new_app_dir / "images")
    monkeypatch.setattr(paths, "VERSIONS_CACHE_PATH", new_app_dir / "versions.json")
    monkeypatch.setattr(paths, "VERSIONS_TXT_CACHE_PATH", new_app_dir / "versions.txt")

    paths.ensure_app_dirs()

    assert not legacy.exists()
    assert (new_app_dir / "library.sqlite3").read_text(encoding="utf-8") == "db"
    assert (new_app_dir / "settings.json").exists()
    assert (new_app_dir / "versions.json").exists()
    assert (new_app_dir / "versions.txt").exists()
    assert (new_app_dir / "images" / "cover.jpg").read_bytes() == b"cover"

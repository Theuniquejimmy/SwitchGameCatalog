import os
import time

from switch_catalog import versions as versions_module
from switch_catalog.versions import (
    file_version_number,
    latest_for_title,
    parse_versions_txt,
    refresh_versions_if_stale,
    released_version_label,
    update_status,
    version_label,
)


def test_short_local_version_maps_to_raw_switch_version():
    assert file_version_number("Example [0100000000000800][v6].nsp") == 393216


def test_dotted_local_version_maps_to_raw_switch_scale():
    assert file_version_number("Example Update v1.3.0.nsp") == 66304
    assert file_version_number("Example Update v3.0.1.nsp") == 196609


def test_version_label_includes_dotted_switch_version():
    assert version_label(196608) == "v196608 (3.0.0)"


def test_released_version_label_omits_empty_release_date_parentheses():
    assert released_version_label(196608, "") == "v196608 (3.0.0)"


def test_update_status_lists_newer_versions():
    versions = {
        "0100000000000000": {
            "65536": "2020-01-01",
            "131072": "2020-02-01",
            "196608": "2020-03-01",
        }
    }

    status, newer = update_status(
        "Example [0100000000000000][v0].nsp",
        ["Example [0100000000000800][v2].nsp"],
        versions,
    )

    assert "Latest Version on File: v131072 (2.0.0)" in status
    assert "Latest Version Released: v196608 (3.0.0)" in status
    assert [(item.version, item.release_date) for item in newer] == [(196608, "2020-03-01")]


def test_update_status_does_not_flag_dotted_update_newer_than_raw_latest():
    versions = {"0100000000000000": {"65536": "2020-01-01"}}

    status, newer = update_status(
        "Example [0100000000000000][v0].nsp",
        ["Example Update v1.3.0.nsp"],
        versions,
    )

    assert status == (
        "Latest Version on File: v66304 (1.3.0)\n"
        "Latest Version Released: v65536 (1.0.0) (2020-01-01)"
    )
    assert newer == []


def test_update_status_does_not_flag_matching_dotted_update_as_needing_update():
    versions = {"0100000000000000": {"196608": "2020-03-01"}}

    status, newer = update_status(
        "Example [0100000000000000][v0].nsp",
        ["Example Update v3.0.0.nsp"],
        versions,
    )

    assert status == (
        "Latest Version on File: v196608 (3.0.0)\n"
        "Latest Version Released: v196608 (3.0.0) (2020-03-01)"
    )
    assert newer == []


def test_update_status_still_compares_raw_switch_versions():
    versions = {"0100000000000000": {"65536": "2020-01-01", "131072": "2020-02-01"}}

    status, newer = update_status(
        "Example [0100000000000000][v0].nsp",
        ["Example Update v65536.nsp"],
        versions,
    )

    assert "Latest Version on File: v65536 (1.0.0)" in status
    assert "Latest Version Released: v131072 (2.0.0)" in status
    assert [(item.version, item.release_date) for item in newer] == [(131072, "2020-02-01")]


def test_update_status_lists_newer_versions_latest_first():
    versions = {
        "0100000000000000": {
            "65536": "2020-01-01",
            "131072": "2020-02-01",
            "196608": "2020-03-01",
        }
    }

    _, newer = update_status(
        "Example [0100000000000000][v0].nsp",
        ["Example Update v65536.nsp"],
        versions,
    )

    assert [item.version for item in newer] == [196608, 131072]


def test_update_status_omits_empty_latest_release_date_parentheses():
    versions = {"0100000000000000": {"196608": ""}}

    status, newer = update_status(
        "Example [0100000000000000][v0].nsp",
        ["Example Update v3.0.0.nsp"],
        versions,
    )

    assert status == (
        "Latest Version on File: v196608 (3.0.0)\n"
        "Latest Version Released: v196608 (3.0.0)"
    )
    assert newer == []


def test_latest_for_title_unknown_when_missing():
    assert latest_for_title({}, "0100000000000000") is None


def test_parse_versions_txt_maps_update_id_to_base_id():
    text = "\n".join(
        [
            "id|rightsId|version",
            "0100000000000000|00000000000000000000000000000000|0",
            "0100000000000800|00000000000000000000000000000000|196608",
        ]
    )

    versions = parse_versions_txt(text)

    assert versions == {
        "0100000000000000": {"196608": ""},
        "0100000000000800": {"196608": ""},
    }


def test_parse_versions_txt_keeps_base_version_zero():
    text = "\n".join(
        [
            "id|rightsId|version",
            "0100000000000000|00000000000000000000000000000000|0",
        ]
    )

    versions = parse_versions_txt(text)

    assert versions == {"0100000000000000": {"0": ""}}


def test_refresh_versions_if_stale_replaces_in_memory_versions(tmp_path, monkeypatch):
    json_cache = tmp_path / "versions.json"
    txt_cache = tmp_path / "versions.txt"
    json_cache.write_text('{"0100000000000000": {"65536": "2020-01-01"}}', encoding="utf-8")
    txt_cache.write_text("id|rightsId|version\n", encoding="utf-8")
    old_time = time.time() - (2 * 24 * 60 * 60)
    os.utime(json_cache, (old_time, old_time))
    os.utime(txt_cache, (old_time, old_time))
    monkeypatch.setattr(versions_module, "VERSIONS_CACHE_PATH", json_cache)
    monkeypatch.setattr(versions_module, "VERSIONS_TXT_CACHE_PATH", txt_cache)

    class FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            pass

    def fake_get(url: str, timeout: int):
        if url.endswith("versions.json"):
            return FakeResponse('{"0100000000000000": {"131072": "2020-02-01"}}')
        return FakeResponse(
            "\n".join(
                [
                    "id|rightsId|version",
                    "0100000000000800|00000000000000000000000000000000|196608",
                ]
            )
        )

    monkeypatch.setattr(versions_module.requests, "get", fake_get)

    refreshed = refresh_versions_if_stale({"0100000000000000": {"65536": "2020-01-01"}})

    assert latest_for_title(refreshed, "0100000000000000").version == 196608

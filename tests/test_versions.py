from switch_catalog.versions import file_version_number, latest_for_title, parse_versions_txt, update_status


def test_short_local_version_maps_to_raw_switch_version():
    assert file_version_number("Example [0100000000000800][v6].nsp") == 393216


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

    assert "Latest: v196608" in status
    assert [(item.version, item.release_date) for item in newer] == [(196608, "2020-03-01")]


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

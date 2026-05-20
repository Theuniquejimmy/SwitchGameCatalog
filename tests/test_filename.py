from switch_catalog.filename import clean_title, detect_version, extract_title_id, is_supported_game_file, title_id_family


def test_clean_base_game_title():
    assert clean_title("Super Mario Odyssey [0100000000010000][v0].xci") == "Super Mario Odyssey"


def test_clean_update_title():
    assert clean_title("Metroid.Dread.Update.2.1.0.NSW.nsp", for_update=True) == "Metroid Dread"


def test_detect_semver_version():
    assert detect_version("Mario Kart 8 Deluxe Update 3.0.1.nsp") == "3.0.1"


def test_detect_integer_version():
    assert detect_version("Example Game Update v65536.nsp") == "65536"


def test_detect_short_bracket_version():
    assert detect_version("Example Game [0100000000000800][v6].nsp") == "6"


def test_extract_switch_title_id_family():
    filename = "Bloomtown [0100AF401C8E4800][v6].nsp"
    assert extract_title_id(filename) == "0100AF401C8E4800"
    assert title_id_family(filename) == "0100AF401C8E"


def test_nsz_is_supported_game_file(tmp_path):
    file_path = tmp_path / "Compressed Game [0100000000000000][v0].nsz"
    file_path.write_bytes(b"nsz")

    assert is_supported_game_file(file_path)

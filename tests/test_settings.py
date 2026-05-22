from switch_catalog.settings import normalize_folder


def test_normalize_folder_preserves_shell_mtp_paths():
    assert normalize_folder("shell:::{device}") == "shell:::{device}"


def test_normalize_folder_adds_shell_prefix_to_parsing_paths():
    assert normalize_folder("::{device}") == "shell:::{device}"

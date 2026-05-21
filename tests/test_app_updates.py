from switch_catalog.app_updates import is_newer_version


def test_is_newer_version_handles_v_tags():
    assert is_newer_version("v0.2.0", "0.1.9")
    assert not is_newer_version("v0.1.0", "0.1.0")

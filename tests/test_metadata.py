from switch_catalog.metadata import metadata_search_queries, normalize_metadata_title, _title_similarity


def test_normalizes_modifier_colon_for_metadata_search():
    assert normalize_metadata_title("Bloomtown꞉ A Different Story") == "Bloomtown: A Different Story"


def test_metadata_queries_include_colonless_variant():
    queries = metadata_search_queries("Bloomtown꞉ A Different Story")
    assert "Bloomtown: A Different Story" in queries
    assert "Bloomtown A Different Story" in queries
    assert "Bloomtown" in queries


def test_bad_metadata_match_scores_low():
    assert _title_similarity("A Musical Story", "Bloomtown: A Different Story") < 0.5

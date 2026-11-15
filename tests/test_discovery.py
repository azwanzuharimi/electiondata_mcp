import pytest

from electiondata_mcp import catalogue as c


def names(results):
    return [r["name"] for r in results]


def test_search_polling_centre_selangor_2018(catalogue):
    assert "saluran_ballots_sgr_se14" in names(
        c.search_datasets(catalogue, "selangor polling centre 2018")
    )


def test_search_by_column(catalogue):
    hits = c.search_datasets(catalogue, "ethnicity")
    assert "voter_demographics" in names(hits)
    assert all(h["matched_on"] for h in hits)


def test_search_boundaries(catalogue):
    hits = names(c.search_datasets(catalogue, "selangor boundary 2018 parliament"))
    assert "map_peninsular_2018_parlimen" in hits


def test_search_group_filter(catalogue):
    hits = c.search_datasets(catalogue, "kedah", group="voter-roll")
    assert hits and all(h["name"].startswith("voter_roll_") for h in hits)


def test_search_no_match(catalogue):
    assert c.search_datasets(catalogue, "zzzz qqqq") == []


def test_describe(catalogue):
    d = c.describe_dataset(catalogue, "headline_stats")
    assert d["links"]["parquet"].endswith("headline_stats.parquet")
    assert "majority" in [col["name"] for col in d["columns"]]
    assert d["join_keys"]


def test_describe_unknown_suggests(catalogue):
    with pytest.raises(KeyError, match="headline_stats"):
        c.describe_dataset(catalogue, "headline_stat")


def test_join_guide_demographics_trap(catalogue):
    g = c.join_guide(catalogue, ["voter_demographics", "headline_stats"])
    assert g["pairs"][0]["on"]
    assert any("822" in t for t in g["traps"])


def test_join_guide_saluran(catalogue):
    g = c.join_guide(catalogue, ["saluran_ballots_ge15", "saluran_stats_ge15"])
    assert set(g["pairs"][0]["on"]) >= {"dm", "pm", "saluran"}


def test_find_dashboard_seat(pages):
    hits = c.find_dashboard(pages, "seat", "lembah pantai")
    assert any("seats/parlimen/p121-lembah-pantai" in h["url"] for h in hits)
    assert all(h["url_ms"].startswith("https://electiondata.my/ms-MY/") for h in hits)


def test_find_dashboard_candidate_by_uid(pages):
    hits = c.find_dashboard(pages, "candidate", "CMVBA")
    assert hits and hits[0]["url"].endswith("/candidates/CMVBA/")


def test_find_dashboard_bad_kind(pages):
    with pytest.raises(ValueError):
        c.find_dashboard(pages, "planet", "x")


def test_data_access(catalogue):
    d = c.data_access(catalogue, names=["headline_stats"])
    assert (
        "https://lake.electiondata.my/results_headline/headline_stats.parquet"
        in d["code"]["python_duckdb"]
    )
    assert "read_parquet" in d["code"]["r"]


def test_data_access_sql(catalogue):
    d = c.data_access(
        catalogue, sql="SELECT * FROM headline_stats WHERE state = 'Selangor'"
    )
    assert "CREATE VIEW headline_stats" in d["code"]["python_duckdb"]

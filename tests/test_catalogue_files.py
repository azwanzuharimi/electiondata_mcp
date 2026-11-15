import json
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "electiondata_mcp"
QUERY_BUILDER = {
    "headline_ballots",
    "headline_stats",
    "voter_demographics",
    "voter_demographics_sabah",
    "voter_demographics_sarawak",
    "saluran_ballots_ge15",
    "saluran_ballots_ge14",
    "saluran_ballots_ge13",
    "saluran_ballots_ge12",
    "saluran_ballots_nsn_se16",
    "saluran_ballots_jhr_se16",
    "saluran_ballots_nsn_se15",
    "saluran_ballots_jhr_se15",
    "saluran_stats_ge15",
    "saluran_stats_ge14",
    "saluran_stats_ge13",
    "saluran_stats_ge12",
    "saluran_stats_nsn_se16",
    "saluran_stats_jhr_se16",
    "saluran_stats_nsn_se15",
    "saluran_stats_jhr_se15",
    "voter_roll_ge15",
    "voter_roll_ge14",
    "voter_roll_ge13",
    "voter_roll_ge12",
    "voter_roll_mlk_se16",
    "voter_roll_nsn_se16",
    "voter_roll_jhr_se16",
    "voter_roll_nsn_se15",
    "voter_roll_jhr_se15",
}


def test_catalogue_has_all_datasets():
    cat = json.loads((PKG / "catalogue.json").read_text())
    names = [e["name"] for e in cat]
    assert len(cat) >= 200
    assert len(names) == len(set(names))
    assert QUERY_BUILDER <= set(names)
    assert all(e["parquet"].startswith("https://lake.electiondata.my/") for e in cat)
    assert all(e["columns"] for e in cat)


def test_pages_index():
    pages = json.loads((PKG / "pages.json").read_text())
    assert len(pages["candidate"]) > 10000
    assert len(pages["seat"]) > 800
    assert any(p.startswith("redelineation/") for p in pages["redelineation"])


def test_all_maps_named_and_streamed():
    cat = json.loads((PKG / "catalogue.json").read_text())
    maps = [e for e in cat if "/maps/" in e["parquet"]]
    assert len(maps) == 43
    assert all(e["name"].startswith("map_") and e["streams"] for e in maps)

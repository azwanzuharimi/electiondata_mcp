import pytest
from test_lookups import raw

from electiondata_mcp import analysis as an


def test_swing_selangor_ph(lake):
    r = an.swing(lake, "GE-14", "GE-15", "PH", state="Selangor")
    rows = {x["seat"]: x for x in r["rows"]}
    kapar = rows["P.109 Kapar"]
    a = raw(
        "SELECT sum(votes_perc) FROM '{D}/headline_ballots.parquet' WHERE election='GE-14' AND seat='P.109 Kapar' AND coalition='PH'"
    )[0][0]
    b = raw(
        "SELECT sum(votes_perc) FROM '{D}/headline_ballots.parquet' WHERE election='GE-15' AND seat='P.109 Kapar' AND coalition='PH'"
    )[0][0]
    assert abs(kapar["share_from"] - a) < 1e-9 and abs(kapar["share_to"] - b) < 1e-9
    assert abs(kapar["change"] - (b - a)) < 1e-9
    assert "unmatched_seats" in r


def test_swing_handles_uncontested(lake):
    r = an.swing(lake, "SE-11", "SE-12", "BN", state="Selangor")
    sementa = next(x for x in r["rows"] if x["seat"] == "N.43 Sementa")
    assert sementa["share_to"] is None and sementa["change"] is None


def test_swing_lists_renamed_seats(lake):
    r = an.swing(lake, "GE-10", "GE-11", "BN", state="W.P. Kuala Lumpur")
    assert r["unmatched_seats"]


def test_marginal_seats_sorted(lake):
    r = an.marginal_seats(lake, "GE-15", state="Selangor", limit=5)
    m = [x["majority_perc"] for x in r["seats"]]
    assert m == sorted(m) and len(m) == 5
    assert all(x["runner_up"] for x in r["seats"])


def test_marginal_seats_threshold(lake):
    r = an.marginal_seats(lake, "GE-15", state="Selangor", max_majority_perc=5)
    assert all(x["majority_perc"] <= 5 for x in r["seats"])


def test_compare_coalitions(lake):
    r = an.compare(lake, "coalition", ["PH", "PN"])
    assert set(r["items"]) == {"PH", "PN"}
    ge15 = next(e for e in r["items"]["PH"]["by_election"] if e["election"] == "GE-15")
    won = raw(
        "SELECT count(*) FROM '{D}/headline_ballots.parquet' WHERE election='GE-15' AND coalition='PH' AND result LIKE 'won%'"
    )[0][0]
    assert ge15["won"] == won


def test_compare_candidates(lake):
    uid = raw(
        "SELECT candidate_uid FROM '{D}/headline_ballots.parquet' WHERE election='GE-15' AND seat='P.121 Lembah Pantai' AND rank=1"
    )[0][0]
    r = an.compare(lake, "candidate", [uid])
    assert r["items"][uid]["totals"]["contests"] >= 1


def test_seat_features(lake):
    r = an.seat_features(lake, "GE-15", state="Selangor")
    n = raw(
        "SELECT count(*) FROM '{D}/headline_stats.parquet' WHERE election='GE-15' AND state='Selangor'"
    )[0][0]
    assert len(r["rows"]) == n
    row = next(x for x in r["rows"] if x["seat"] == "P.109 Kapar")
    assert row["voters_total"] > 0 and row["winner_coalition"]
    assert row["prev_election"] == "GE-14" and row["name_changed"] is False
    assert row["demo_voters_total"] > 0
    assert any(k.startswith("share_") for k in row)


def test_seat_features_late_polling_seat_prev_is_not_itself(lake):
    r = an.seat_features(lake, "SE-15", state="Selangor", level="dun")
    assert all(x["prev_election"] != "SE-15" for x in r["rows"])


@pytest.mark.live
def test_live_seat_features_padang_serai():
    from electiondata_mcp.catalogue import load_catalogue
    from electiondata_mcp.lake import Lake

    r = an.seat_features(Lake(load_catalogue()), "GE-15", state="Kedah")
    ps = next(x for x in r["rows"] if x["seat"].endswith("Padang Serai"))
    assert ps["date"] == "2022-12-07" and ps["prev_election"] == "GE-14"

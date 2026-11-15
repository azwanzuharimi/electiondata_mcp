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
    m = [x["majority"] for x in r["seats"]]
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


def test_seat_features_prev_covers_all_dates_of_previous_election(lake):
    r = an.seat_features(lake, "SE-06", state="Selangor", level="dun")
    assert {x["prev_election"] for x in r["rows"]} == {"SE-05"}
    expected = raw("""SELECT count(*) FROM '{D}/headline_stats.parquet' a WHERE a.election='SE-06' AND a.state='Selangor'
        AND a.seat IN (SELECT seat FROM '{D}/headline_stats.parquet' WHERE election='SE-05' AND state='Selangor')""")[0][0]
    assert sum(not x["name_changed"] for x in r["rows"]) == expected > 1


def test_swing_seat_not_contested_by_group_is_left_out(lake):
    r = an.swing(lake, "GE-14", "GE-15", "PN", state="Selangor")
    assert r["summary"]["seats_compared"] == 0
    assert all(x["change"] is None for x in r["rows"])
    assert any(x["stood_from"] is False for x in r["rows"])


def test_swing_unknown_group_errors(lake):
    with pytest.raises(ValueError, match="PHH"):
        an.swing(lake, "GE-14", "GE-15", "PHH", state="Selangor")


def test_compare_state_elections_split_by_state(lake):
    r = an.compare(lake, "party", ["DAP"])
    keys = [(e["election"], e["state"]) for e in r["items"]["DAP"]["by_election"]]
    assert len(keys) == len(set(keys))
    assert all(e["state"] == "Malaysia" for e in r["items"]["DAP"]["by_election"] if e["election"].startswith("GE"))


def test_compare_unknown_errors(lake):
    with pytest.raises(ValueError, match="NOPE"):
        an.compare(lake, "party", ["NOPE"])


def test_marginal_seats_tie_for_second_is_one_row(lake):
    r = an.marginal_seats(lake, "GE-15", limit=500)
    seats = [(x["state"], x["seat"]) for x in r["seats"]]
    assert len(seats) == len(set(seats))



@pytest.mark.live
def test_live_marginal_seats_tie_for_second_mukah():
    from electiondata_mcp.catalogue import load_catalogue
    from electiondata_mcp.lake import Lake

    r = an.marginal_seats(Lake(load_catalogue()), "GE-13", state="Sarawak", limit=100)
    seats = [x["seat"] for x in r["seats"]]
    assert len(seats) == len(set(seats)) == 31
    mukah = next(x for x in r["seats"] if x["seat"] == "P.213 Mukah")
    assert " / " in mukah["runner_up"] and mukah["runner_up_tied"] == 2


@pytest.mark.live
def test_live_marginal_ge15_smallest_majority_in_votes_first():
    from electiondata_mcp.catalogue import load_catalogue
    from electiondata_mcp.lake import Lake

    r = an.marginal_seats(Lake(load_catalogue()), "GE-15", limit=5)
    assert [(s["seat"], s["majority"]) for s in r["seats"]][:2] == [("P.203 Lubok Antu", 100), ("P.173 Putatan", 124)]

import duckdb
import pytest
from conftest import DATA

from electiondata_mcp import lookups as lk


def raw(sql: str):
    return duckdb.sql(sql.replace("{D}", str(DATA).replace("\\", "/"))).fetchall()


def test_resolve_seat_loose_text(lake):
    assert ("P.121 Lembah Pantai", "W.P. Kuala Lumpur") in lk.resolve_seat(
        lake, "lembah pantai"
    )
    assert lk.resolve_seat(lake, "P.121 LEMBAH PANTAI") == [
        ("P.121 Lembah Pantai", "W.P. Kuala Lumpur")
    ]


def test_search_finds_seat_and_candidate(lake):
    r = lk.search(lake, "lembah pantai")
    assert r["seats"][0]["seat"] == "P.121 Lembah Pantai"
    name = raw(
        "SELECT name FROM '{D}/headline_ballots.parquet' WHERE election='GE-15' AND seat='P.121 Lembah Pantai' AND rank=1"
    )[0][0]
    r2 = lk.search(lake, name.split()[0] + " " + name.split()[1], kind="candidate")
    assert any(c["name"] == name for c in r2["candidates"])


def test_contest_result_matches_raw(lake):
    r = lk.contest_result(lake, "GE-15", "lembah pantai")
    c = r["contests"][0]
    winner = raw(
        "SELECT name, votes FROM '{D}/headline_ballots.parquet' WHERE election='GE-15' AND seat='P.121 Lembah Pantai' AND rank=1"
    )[0]
    assert (c["ballot"][0]["name"], c["ballot"][0]["votes"]) == winner
    assert (
        c["stats"]["majority"]
        == raw(
            "SELECT majority FROM '{D}/headline_stats.parquet' WHERE election='GE-15' AND seat='P.121 Lembah Pantai'"
        )[0][0]
    )


def test_seat_history_newest_first(lake):
    r = lk.seat_history(lake, "lembah pantai")
    dates = [h["date"] for h in r["history"]]
    assert dates == sorted(dates, reverse=True)
    assert r["history"][0]["election"] in ("GE-15", "BY-ELECTION")


def test_seat_history_ambiguous(lake):
    r = lk.seat_history(lake, "lembah")
    assert "ambiguous" in r


def test_candidate_profile(lake):
    uid = raw(
        "SELECT candidate_uid FROM '{D}/headline_ballots.parquet' WHERE election='GE-15' AND seat='P.121 Lembah Pantai' AND rank=1"
    )[0][0]
    r = lk.candidate_profile(lake, uid)
    n = raw(
        f"SELECT count(*) FROM '{{D}}/headline_ballots.parquet' WHERE candidate_uid='{uid}'"
    )[0][0]
    assert r["totals"]["contests"] == n == len(r["contests"])


def test_candidate_profile_unknown(lake):
    with pytest.raises(KeyError):
        lk.candidate_profile(lake, "NOPE0")


def test_election_summary_state_elections_split_by_state(lake):
    r = lk.election_summary(lake, "SE-15")
    assert list(r["by_state"]) == ["Selangor"]
    seats = raw(
        "SELECT count(*) FROM '{D}/headline_stats.parquet' WHERE election='SE-15' AND state='Selangor'"
    )[0][0]
    assert (
        sum(x["seats_won"] for x in r["by_state"]["Selangor"]["by_coalition"]) == seats
    )


def test_election_summary_ge_scope(lake):
    r = lk.election_summary(lake, "GE-15", state="Selangor")
    seats = raw(
        "SELECT count(*) FROM '{D}/headline_stats.parquet' WHERE election='GE-15' AND state='Selangor'"
    )[0][0]
    assert sum(x["seats_won"] for x in r["by_state"]["Selangor"]["by_party"]) == seats


def test_uncontested_seat_does_not_break_summary(lake):
    r = lk.election_summary(lake, "SE-12", state="Selangor")
    s = r["by_state"]["Selangor"]
    assert s["uncontested_seats"] >= 1
    assert s["turnout_perc"] is not None


def test_byelections(lake):
    r = lk.byelections(lake, date_from="2020-01-01")
    assert r["byelections"]
    assert all(b["date"] >= "2020-01-01" for b in r["byelections"])


def test_seat_demographics(lake):
    r = lk.seat_demographics(lake, "lembah pantai")
    assert r["demographics"]["voters_total"] > 0
    assert r["election"].startswith(("GE", "SE"))


def test_polling_centre_results(lake):
    r = lk.polling_centre_results(lake, "GE-15", "lembah pantai", level="dm")
    total = raw(
        "SELECT sum(votes) FROM '{D}/saluran_ballots_ge15.parquet' WHERE seat='P.121 Lembah Pantai'"
    )[0][0]
    assert sum(row["votes"] for row in r["rows"]) == total


def test_polling_centre_results_missing_election(lake):
    r = lk.polling_centre_results(lake, "GE-05", "lembah pantai")
    assert "no polling-centre data" in r["note"]


def test_exact_match_is_labelled(lake):
    assert lk.seat_history(lake, "lembah pantai")["seat_match"] == "exact name"
    assert lk.contest_result(lake, "GE-15", "P121", state="Kuala Lumpur")["seat_match"] == "code"
    assert "ambiguous" in lk.contest_result(lake, "GE-15", "P121")


def test_partial_match_warns(lake):
    assert "ambiguous" in lk.seat_history(lake, "Kapa")
    r = lk.seat_history(lake, "P.109 Kapa")
    assert r["seat_match"] == "partial name"
    assert "Confirm" in r["warning"]

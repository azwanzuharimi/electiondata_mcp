import pytest

from electiondata_mcp.catalogue import load_catalogue
from electiondata_mcp.lake import Lake

pytestmark = pytest.mark.live
CAT = load_catalogue()
SALURAN = sorted(e["name"].removeprefix("saluran_ballots_") for e in CAT if e["name"].startswith("saluran_ballots_"))
KEY = "date, election, state, seat"


@pytest.fixture(scope="module")
def live():
    return Lake(CAT)


def one(live, sql):
    return live.query(sql)["rows"][0][0]


def test_every_contest_has_stats_and_ballots(live):
    assert one(live, f"SELECT count(*) FROM headline_stats") == one(live, f"SELECT count(*) FROM (SELECT DISTINCT {KEY} FROM headline_ballots)")


def test_one_winner_per_contest(live):
    assert one(live, f"SELECT count(*) FROM (SELECT {KEY}, count(*) FILTER (WHERE result LIKE 'won%') w FROM headline_ballots GROUP BY ALL) WHERE w <> 1") == 0


def test_rank_one_is_winner(live):
    assert one(live, "SELECT count(*) FROM headline_ballots WHERE rank = 1 AND result NOT LIKE 'won%'") == 0


def test_majority_is_winner_minus_runner_up(live):
    assert one(live, f"""SELECT count(*) FROM headline_stats s JOIN (
        SELECT {KEY}, max(votes) FILTER (WHERE rank = 1) - max(votes) FILTER (WHERE rank = 2) m FROM headline_ballots GROUP BY ALL) x USING ({KEY})
        WHERE s.n_candidates > 1 AND s.majority <> x.m""") == 0


def test_valid_votes_equal_candidate_votes(live):
    assert one(live, f"""SELECT count(*) FROM headline_stats s JOIN (SELECT {KEY}, sum(votes) v, count(*) c FROM headline_ballots GROUP BY ALL) x USING ({KEY})
        WHERE (s.n_candidates > 1 AND s.votes_valid <> x.v) OR s.n_candidates <> x.c""") == 0


def test_shares_sum_to_100(live):
    assert one(live, f"SELECT count(*) FROM (SELECT {KEY}, sum(votes_perc) p FROM headline_ballots WHERE result <> 'won_uncontested' GROUP BY ALL) WHERE abs(p - 100) > 0.01") == 0


def test_turnout_and_majority_formulas(live):
    assert one(live, """SELECT count(*) FROM headline_stats WHERE n_candidates > 1 AND (
        abs(voter_turnout - 100.0 * ballots_issued / voters_total) > 0.001 OR abs(majority_perc - 100.0 * majority / votes_valid) > 0.001)""") == 0


@pytest.mark.parametrize("scope", SALURAN)
def test_polling_centre_totals_match_seat_totals(live, scope):
    name = f"saluran_ballots_{scope}"
    live.ensure([name])
    bad = live.query(f"""WITH sal AS (SELECT election, state, seat, candidate_uid, sum(votes) v FROM {name} GROUP BY ALL)
        SELECT h.seat, h.candidate_uid, h.votes, sal.v FROM sal JOIN headline_ballots h USING (election, state, seat, candidate_uid)
        WHERE h.votes <> sal.v AND h.election <> 'BY-ELECTION' LIMIT 5""")
    assert bad["rows"] == [], f"{name}: {bad['rows']}"


def test_ge15_published_seat_counts(live):
    from electiondata_mcp.lookups import election_summary

    seats = {r["coalition"]: r["seats_won"] for r in election_summary(live, "GE-15")["by_state"]["Malaysia"]["by_coalition"]}
    assert (seats["PH"], seats["PN"], seats["BN"], seats["GPS"], seats["GRS"]) == (82, 74, 30, 23, 6)
    assert sum(seats.values()) == 222

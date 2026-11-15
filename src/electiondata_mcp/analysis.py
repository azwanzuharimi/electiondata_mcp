from electiondata_mcp.lake import Lake
from electiondata_mcp.lookups import CREDIT, _norm_state, candidate_profile

GROUP_COL = {"coalition": "coalition", "party": "party"}


def _scope(state: str | None, alias: str = "") -> tuple[str, list]:
    return (f" AND {alias}state = ?", [state]) if state else ("", [])


def swing(
    lake: Lake,
    election_from: str,
    election_to: str,
    group: str,
    state: str | None = None,
    by: str = "coalition",
) -> dict:
    col = GROUP_COL.get(by)
    if not col:
        raise ValueError("by must be 'coalition' or 'party'")
    st = _norm_state(lake, state)
    where, p = _scope(st)
    sql = f"""
    WITH seats AS (
        SELECT election, state, seat, bool_and(result <> 'won_uncontested') AS contested,
               sum(votes_perc) FILTER (WHERE upper({col}) = upper(?)) AS share
        FROM headline_ballots WHERE election IN (?, ?) {where} GROUP BY ALL
    ),
    a AS (SELECT * FROM seats WHERE election = ?), b AS (SELECT * FROM seats WHERE election = ?)
    SELECT coalesce(a.state, b.state) AS state, coalesce(a.seat, b.seat) AS seat,
           CASE WHEN a.contested THEN coalesce(a.share, 0) END AS share_from,
           CASE WHEN b.contested THEN coalesce(b.share, 0) END AS share_to,
           a.seat IS NULL AS only_in_to, b.seat IS NULL AS only_in_from
    FROM a FULL JOIN b USING (state, seat) ORDER BY state, seat
    """
    e1, e2 = election_from.upper(), election_to.upper()
    rows = lake.records(sql, [group, e1, e2, *p, e1, e2])
    matched, unmatched = [], []
    for r in rows:
        if r["only_in_to"] or r["only_in_from"]:
            unmatched.append(
                {
                    "seat": r["seat"],
                    "state": r["state"],
                    "only_in": e2 if r["only_in_to"] else e1,
                }
            )
            continue
        change = (
            r["share_to"] - r["share_from"]
            if r["share_from"] is not None and r["share_to"] is not None
            else None
        )
        matched.append(
            {
                "state": r["state"],
                "seat": r["seat"],
                "share_from": r["share_from"],
                "share_to": r["share_to"],
                "change": change,
            }
        )
    changes = [r["change"] for r in matched if r["change"] is not None]
    return {
        "scope": f"{by} {group}: vote share per seat in {e1} and {e2}"
        + (f" ({st})" if st else "")
        + ". Seats matched by exact seat name; renamed or new seats are in unmatched_seats. Uncontested seats have null share. "
        + CREDIT,
        "summary": {
            "seats_compared": len(changes),
            "mean_change": round(sum(changes) / len(changes), 2) if changes else None,
        },
        "rows": matched,
        "unmatched_seats": unmatched,
    }


def marginal_seats(
    lake: Lake,
    election: str,
    state: str | None = None,
    max_majority_perc: float | None = None,
    max_majority: int | None = None,
    limit: int = 30,
) -> dict:
    st = _norm_state(lake, state)
    where, p = _scope(st, "s.")
    if max_majority_perc is not None:
        where += " AND s.majority_perc <= ?"
        p.append(max_majority_perc)
    if max_majority is not None:
        where += " AND s.majority <= ?"
        p.append(max_majority)
    rows = lake.records(
        f"""SELECT s.state, s.seat, s.date, s.majority, s.majority_perc, s.voter_turnout AS turnout_perc,
                   w.name AS winner, w.party AS winner_party, w.coalition AS winner_coalition,
                   r.name AS runner_up, r.party AS runner_up_party, r.coalition AS runner_up_coalition
            FROM headline_stats s
            JOIN headline_ballots w ON w.date = s.date AND w.election = s.election AND w.state = s.state AND w.seat = s.seat AND w.rank = 1
            JOIN headline_ballots r ON r.date = s.date AND r.election = s.election AND r.state = s.state AND r.seat = s.seat AND r.rank = 2
            WHERE s.election = ? AND s.n_candidates > 1 {where}
            ORDER BY s.majority_perc, s.majority LIMIT ?""",
        [election.upper(), *p, limit],
    )
    return {
        "scope": f"Closest {election.upper()} contests"
        + (f" in {st}" if st else "")
        + ", smallest majority first. Uncontested seats left out. "
        + CREDIT,
        "seats": rows,
    }


def compare(lake: Lake, kind: str, ids: list[str]) -> dict:
    if kind == "candidate":
        return {
            "scope": "Candidates side by side. " + CREDIT,
            "items": {i: candidate_profile(lake, i) for i in ids},
        }
    col = {"party": "party", "coalition": "coalition"}.get(kind)
    if not col:
        raise ValueError("kind must be 'candidate', 'party' or 'coalition'")
    items = {}
    for i in ids:
        match = f"(upper({col}) = upper(?) OR upper({col}_uid::VARCHAR) = upper(?))"
        rows = lake.records(
            f"""WITH mine AS (SELECT * FROM headline_ballots WHERE {match} AND election <> 'BY-ELECTION'),
                     t AS (SELECT election, sum(votes) AS all_votes FROM headline_ballots
                           WHERE (election, state) IN (SELECT DISTINCT election, state FROM mine) GROUP BY election)
                SELECT m.election, min(m.date) AS date, count(*) AS contests, count(*) FILTER (WHERE result LIKE 'won%') AS won,
                       count(*) FILTER (WHERE result = 'lost_deposit') AS lost_deposit, sum(m.votes) AS votes,
                       round(100.0 * sum(m.votes) / any_value(t.all_votes), 2) AS votes_perc_of_states_contested
                FROM mine m JOIN t USING (election)
                GROUP BY m.election ORDER BY date DESC""",
            [i, i],
        )
        items[i] = {
            "totals": {
                k: sum(r[k] for r in rows) for k in ("contests", "won", "lost_deposit")
            },
            "by_election": rows,
        }
    return {
        "scope": f"{kind}s side by side per election (general and state elections; by-elections left out). "
        "votes_perc_of_states_contested = share of all votes in the states where it stood. "
        + CREDIT,
        "items": items,
    }


def seat_features(
    lake: Lake,
    election: str,
    state: str | None = None,
    level: str = "parlimen",
    limit: int = 2000,
) -> dict:
    if level not in ("parlimen", "dun"):
        raise ValueError("level must be 'parlimen' or 'dun'")
    st = _norm_state(lake, state)
    election = election.upper()
    prefix = "P.%" if level == "parlimen" else "N.%"
    where, p = _scope(st, "s.")
    kind = election.split("-")[0]
    rows = lake.records(
        f"""
        WITH cur AS (
            SELECT s.*, w.party AS winner_party, w.coalition AS winner_coalition, w.result = 'won_uncontested' AS uncontested
            FROM headline_stats s JOIN headline_ballots w USING (date, election, state, seat)
            WHERE s.election = ? AND s.seat LIKE ? AND w.rank = 1 {where}
        ),
        prev_el AS (
            SELECT c.state, max(h.date) AS prev_date FROM cur c
            JOIN headline_stats h ON h.state = c.state AND h.date < c.date AND h.election LIKE ? AND h.election <> c.election AND h.seat LIKE ?
            GROUP BY c.state
        ),
        prev AS (
            SELECT h.state, h.seat, h.election AS prev_election, h.majority_perc AS prev_majority_perc, h.voter_turnout AS prev_turnout_perc,
                   w.coalition AS prev_winner_coalition, w.party AS prev_winner_party
            FROM headline_stats h JOIN prev_el pe ON pe.state = h.state AND pe.prev_date = h.date
            JOIN headline_ballots w ON w.date = h.date AND w.election = h.election AND w.state = h.state AND w.seat = h.seat AND w.rank = 1
            WHERE h.election LIKE ?
        ),
        demo AS (
            SELECT c.state, c.seat, d.* EXCLUDE (date, election, state, seat)
            FROM cur c ASOF LEFT JOIN voter_demographics d ON d.state = c.state AND d.seat = c.seat AND c.date >= d.date
        )
        SELECT c.state, c.seat, c.date, c.voters_total, c.voter_turnout AS turnout_perc, c.majority_perc, c.n_candidates, c.uncontested,
               c.winner_party, c.winner_coalition,
               coalesce(pr.prev_election, (SELECT any_value(h.election) FROM headline_stats h JOIN prev_el pe ON pe.state = h.state AND pe.prev_date = h.date WHERE h.state = c.state)) AS prev_election,
               pr.seat IS NULL AS name_changed, pr.prev_winner_coalition, pr.prev_winner_party, pr.prev_majority_perc, pr.prev_turnout_perc,
               COLUMNS(d.* EXCLUDE (state, seat)) AS "demo_\\0"
        FROM cur c LEFT JOIN prev pr USING (state, seat) LEFT JOIN demo d USING (state, seat)
        ORDER BY c.state, c.seat
        """,
        [election, prefix, *p, kind + "-%", prefix, kind + "-%"],
        limit=limit,
    )
    shares = lake.records(
        f"""SELECT state, seat, coalition, sum(votes_perc) AS share FROM headline_ballots s
            WHERE election = ? AND seat LIKE ? {where} GROUP BY ALL""",
        [election, prefix, *p],
    )
    by_seat: dict[tuple, dict] = {}
    for s in shares:
        by_seat.setdefault((s["state"], s["seat"]), {})[f"share_{s['coalition']}"] = s[
            "share"
        ]
    cols = sorted({k for v in by_seat.values() for k in v})
    for r in rows:
        r.update(
            {
                c: by_seat.get((r["state"], r["seat"]), {}).get(
                    c, 0.0 if not r["uncontested"] else None
                )
                for c in cols
            }
        )
    return {
        "scope": f"One row per {level} seat in {election}"
        + (f" ({st})" if st else "")
        + ". prev_* = same seat name in the previous election of the same kind in that state; name_changed = no match. "
        "demo_* = latest voter roll on or before polling day. share_* = vote share per coalition. "
        + CREDIT,
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows,
    }

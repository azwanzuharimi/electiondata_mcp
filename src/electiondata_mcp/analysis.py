from electiondata_mcp.lake import Lake, check_limit
from electiondata_mcp.lookups import (
    CREDIT,
    _norm_state,
    candidate_profile,
    check_election,
    data_warnings,
    row_warnings,
    warn,
)

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
               bool_or(upper({col}) = upper(?)) AS stood,
               sum(votes_perc) FILTER (WHERE upper({col}) = upper(?)) AS share
        FROM headline_ballots WHERE election IN (?, ?) {where} GROUP BY ALL
    ),
    a AS (SELECT * FROM seats WHERE election = ?), b AS (SELECT * FROM seats WHERE election = ?)
    SELECT coalesce(a.state, b.state) AS state, coalesce(a.seat, b.seat) AS seat,
           CASE WHEN a.contested AND a.stood THEN a.share END AS share_from,
           CASE WHEN b.contested AND b.stood THEN b.share END AS share_to,
           a.stood AS stood_from, b.stood AS stood_to,
           a.seat IS NULL AS only_in_to, b.seat IS NULL AS only_in_from
    FROM a FULL JOIN b USING (state, seat) ORDER BY state, seat
    """
    e1, e2 = check_election(lake, election_from, st), check_election(lake, election_to, st)
    rows = lake.records(sql, [group, group, e1, e2, *p, e1, e2])
    if not any(r["stood_from"] or r["stood_to"] for r in rows):
        raise ValueError(f"'{group}' did not stand in {e1} or {e2}" + (f" in {st}" if st else "") + f". Check the {by} name with search.")
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
                "stood_from": r["stood_from"],
                "stood_to": r["stood_to"],
            }
        )
    changes = [r["change"] for r in matched if r["change"] is not None]
    return warn({
        "scope": f"{by} {group}: vote share per seat in {e1} and {e2}"
        + (f" ({st})" if st else "")
        + ". Seats matched by exact seat name; renamed or new seats are in unmatched_seats. Share is null where the group did not stand or the seat was uncontested; change only where it stood in both. "
        + CREDIT,
        "summary": {
            "seats_compared": len(changes),
            "seats_stood_from": sum(1 for r in matched if r["stood_from"]),
            "seats_stood_to": sum(1 for r in matched if r["stood_to"]),
            "mean_change": round(sum(changes) / len(changes), 2) if changes else None,
        },
        "rows": matched,
        "unmatched_seats": unmatched,
    }, row_warnings(matched, e1) + [w for w in row_warnings(matched, e2) if w not in row_warnings(matched, e1)])


def marginal_seats(
    lake: Lake,
    election: str,
    state: str | None = None,
    max_majority_perc: float | None = None,
    max_majority: int | None = None,
    limit: int = 30,
) -> dict:
    check_limit(limit)
    st = _norm_state(lake, state)
    election = check_election(lake, election, st)
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
                   r.runner_up, r.runner_up_party, r.runner_up_coalition, r.runner_up_tied
            FROM headline_stats s
            JOIN headline_ballots w ON w.date = s.date AND w.election = s.election AND w.state = s.state AND w.seat = s.seat AND w.rank = 1
            JOIN (SELECT date, election, state, seat, string_agg(name, ' / ' ORDER BY name) AS runner_up,
                         string_agg(party, ' / ' ORDER BY name) AS runner_up_party, string_agg(coalition, ' / ' ORDER BY name) AS runner_up_coalition,
                         count(*) AS runner_up_tied
                  FROM headline_ballots WHERE rank = 2 GROUP BY ALL) r
              ON r.date = s.date AND r.election = s.election AND r.state = s.state AND r.seat = s.seat
            WHERE s.election = ? AND s.n_candidates > 1 {where}
            ORDER BY s.majority, s.majority_perc, s.seat LIMIT ?""",
        [election, *p, limit],
    )
    return warn({
        "scope": f"Closest {election} contests"
        + (f" in {st}" if st else "")
        + ", smallest majority in votes first (majority_perc gives it as a share of valid votes). Uncontested seats left out. runner_up_tied > 1 means several candidates tied for second. "
        + CREDIT,
        "seats": rows,
    }, row_warnings(rows, election))


def compare(lake: Lake, kind: str, ids: list[str]) -> dict:
    if kind == "candidate":
        items = {i: candidate_profile(lake, i) for i in ids}
        return warn({
            "scope": "Candidates side by side. " + CREDIT,
            "items": items,
        }, list(dict.fromkeys(w for v in items.values() for w in v.get("data_warnings", []))))
    col = {"party": "party", "coalition": "coalition"}.get(kind)
    if not col:
        raise ValueError("kind must be 'candidate', 'party' or 'coalition'")
    items = {}
    notes: list[str] = []
    for i in ids:
        match = f"(upper({col}) = upper(?) OR upper({col}_uid::VARCHAR) = upper(?))"
        rows = lake.records(
            f"""WITH mine AS (SELECT *, CASE WHEN election LIKE 'GE-%' THEN 'Malaysia' ELSE state END AS scope
                              FROM headline_ballots WHERE {match} AND election <> 'BY-ELECTION'),
                     t AS (SELECT b.election, CASE WHEN b.election LIKE 'GE-%' THEN 'Malaysia' ELSE b.state END AS scope, sum(b.votes) AS all_votes
                           FROM headline_ballots b WHERE (b.election, b.state) IN (SELECT DISTINCT election, state FROM mine) GROUP BY ALL)
                SELECT m.election, m.scope AS state, min(m.date) AS date, count(*) AS contests, count(*) FILTER (WHERE result LIKE 'won%') AS won,
                       count(*) FILTER (WHERE result = 'lost_deposit') AS lost_deposit, sum(m.votes) AS votes,
                       round(100.0 * sum(m.votes) / any_value(t.all_votes), 2) AS votes_perc_of_states_contested
                FROM mine m JOIN t USING (election, scope)
                GROUP BY m.election, m.scope ORDER BY date DESC, state""",
            [i, i],
        )
        if not rows:
            raise ValueError(f"No {kind} named '{i}' in general or state elections. Check the name with search.")
        notes += [w for r in rows for w in data_warnings(r["election"], None if r["state"] == "Malaysia" else [r["state"]])]
        items[i] = {
            "totals": {
                k: sum(r[k] for r in rows) for k in ("contests", "won", "lost_deposit")
            },
            "by_election": rows,
        }
    return warn({
        "scope": f"{kind}s side by side: one row per general election (state = Malaysia) and one row per state election per state; by-elections left out. "
        "votes_perc_of_states_contested = share of all votes in the states where it stood. "
        + CREDIT,
        "items": items,
    }, list(dict.fromkeys(notes)))


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
    election = check_election(lake, election, st)
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
        first_day AS (SELECT state, min(date) AS d FROM cur GROUP BY state),
        prev_el AS (
            SELECT f.state, arg_max(h.election, h.date) AS prev_election FROM first_day f
            JOIN headline_stats h ON h.state = f.state AND h.date < f.d AND h.election LIKE ? AND h.election <> ? AND h.seat LIKE ?
            GROUP BY f.state
        ),
        prev AS (
            SELECT h.state, h.seat, h.election AS prev_election, h.majority_perc AS prev_majority_perc, h.voter_turnout AS prev_turnout_perc,
                   w.coalition AS prev_winner_coalition, w.party AS prev_winner_party
            FROM headline_stats h JOIN prev_el pe ON pe.state = h.state AND pe.prev_election = h.election
            JOIN headline_ballots w ON w.date = h.date AND w.election = h.election AND w.state = h.state AND w.seat = h.seat AND w.rank = 1
            WHERE h.seat LIKE ?
        ),
        demo AS (
            SELECT c.state, c.seat, d.date AS roll_date, d.election AS roll_election, d.* EXCLUDE (date, election, state, seat)
            FROM cur c ASOF LEFT JOIN voter_demographics d ON d.state = c.state AND d.seat = c.seat AND c.date >= d.date
        )
        SELECT c.state, c.seat, c.date, c.voters_total, c.voter_turnout AS turnout_perc, c.majority_perc, c.n_candidates, c.uncontested,
               c.winner_party, c.winner_coalition,
               pe.prev_election,
               pr.seat IS NULL AS name_changed, pr.prev_winner_coalition, pr.prev_winner_party, pr.prev_majority_perc, pr.prev_turnout_perc,
               COLUMNS(d.* EXCLUDE (state, seat)) AS "demo_\\0"
        FROM cur c LEFT JOIN prev_el pe USING (state) LEFT JOIN prev pr USING (state, seat) LEFT JOIN demo d USING (state, seat)
        ORDER BY c.state, c.seat
        """,
        [election, prefix, *p, kind + "-%", election, prefix, prefix],
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
    return warn({
        "scope": f"One row per {level} seat in {election}"
        + (f" ({st})" if st else "")
        + ". prev_* = same seat name in the previous election of the same kind in that state; name_changed = no match. "
        "demo_* = latest voter roll on or before polling day; check demo_roll_date and demo_roll_election, it can be an older roll or null. share_* = vote share per coalition. "
        + CREDIT,
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows,
    }, row_warnings(rows, election))

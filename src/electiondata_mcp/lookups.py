import datetime as dt
import re

from electiondata_mcp.lake import Lake, check_limit

STATE_CODES = {
    "Johor": "jhr",
    "Kedah": "kdh",
    "Kelantan": "ktn",
    "Melaka": "mlk",
    "Negeri Sembilan": "nsn",
    "Pahang": "phg",
    "Perlis": "pls",
    "Pulau Pinang": "png",
    "Perak": "prk",
    "Sabah": "sbh",
    "Selangor": "sgr",
    "Sarawak": "swk",
    "Terengganu": "trg",
}
SEAT_NAME = "lower(regexp_replace(seat, '^[PN]\\.\\d+[A-Z]?\\s+', ''))"
CODE_RE = re.compile(r"^\s*([PNpn])\.?\s*0*(\d+)([A-Za-z]?)\b\s*(.*)$")
CREDIT = "Data: ElectionData.MY (MECo), CC0."
KNOWN_ISSUES = [
    {
        "election": "GE-15",
        "state": "Kelantan",
        "seat": "P.028 Pasir Puteh",
        "tables": [
            "headline_ballots",
            "headline_stats",
            "headline_ballots_federal",
            "headline_stats_federal",
            "saluran_ballots_ge15",
            "saluran_stats_ge15",
        ],
        "note": "GE-15 P.028 Pasir Puteh differs from the official SPR gazette P.U. (B) 607/2022. "
        "Gazette: BN 23,988 votes, 81,319 valid votes, 82,576 ballots issued, 1,020 rejected, majority 29,120. "
        "This data: 23,844, 81,175, 82,425, 1,013 and 29,264. "
        "So GE-15 totals for Malaysia and Kelantan that include this seat are short by 144 BN votes, 144 valid votes, 151 ballots issued and 7 rejected votes, "
        "and Kelantan turnout reads 71.67% instead of 71.68%. "
        "Use the gazette figures for this seat.",
    }
]


def data_warnings(
    election: str | None, states: list[str] | None = None, seats: list[str] | None = None
) -> list[str]:
    e = election.strip().upper() if election else None
    return [
        k["note"]
        for k in KNOWN_ISSUES
        if (e is None or k["election"] == e)
        and (states is None or k["state"] in states)
        and (seats is None or k["seat"] in seats)
    ]


def row_warnings(rows: list[dict], election: str | None = None) -> list[str]:
    out: list[str] = []
    for r in rows:
        for w in data_warnings(election or r.get("election"), [r.get("state")], [r.get("seat")]):
            if w not in out:
                out.append(w)
    return out


def warn(result: dict, notes: list[str]) -> dict:
    if notes:
        result["data_warnings"] = notes
    return result


def check_election(lake: Lake, election: str, state: str | None = None) -> str:
    e = election.strip().upper()
    have = [
        r["election"]
        for r in lake.records(
            "SELECT election FROM headline_stats"
            + (" WHERE state = ?" if state else "")
            + " GROUP BY election ORDER BY min(date)",
            [state] if state else [],
        )
    ]
    if e not in have:
        where = f" in {state}" if state else ""
        raise ValueError(f"No election '{e}'{where}. Elections{where} in the data: {', '.join(have)}.")
    return e


def check_date(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be a date as YYYY-MM-DD, for example 2022-11-19.") from None


def _norm_state(lake: Lake, state: str | None) -> str | None:
    if not state or state.lower() == "malaysia":
        return None
    rows = lake.records(
        "SELECT DISTINCT state FROM headline_stats WHERE lower(state) = lower(?) OR lower(replace(state, 'W.P. ', '')) = lower(?)",
        [state, state],
    )
    if not rows:
        raise KeyError(f"Unknown state '{state}'.")
    return rows[0]["state"]


def resolve_seat(lake: Lake, seat: str, state: str | None = None) -> list[tuple[str, str]]:
    return _resolve(lake, seat, state)[0]


def _resolve(lake: Lake, seat: str, state: str | None = None) -> tuple[list[tuple[str, str]], str]:
    state = _norm_state(lake, state)
    how = "exact name"
    m = CODE_RE.match(seat)
    if m and m.group(2):
        code = f"{m.group(1).upper()}.{int(m.group(2)):0{3 if m.group(1).upper() == 'P' else 2}d}{m.group(3).upper()}"
        rest = m.group(4).strip().lower()
        sql = (
            "SELECT DISTINCT seat, state FROM headline_stats WHERE seat LIKE ? AND (? = '' OR "
            + SEAT_NAME
            + " = ?)"
        )
        params = [code + " %", rest, rest]
        how = "code and name" if rest else "code"
    else:
        sql = (
            "SELECT DISTINCT seat, state FROM headline_stats WHERE "
            + SEAT_NAME
            + " = lower(trim(?))"
        )
        params = [seat]
    if state:
        sql += " AND state = ?"
        params.append(state)
    rows = lake.records(sql + " ORDER BY seat", params)
    if not rows:
        how = "partial name"
        rows = lake.records(
            "SELECT DISTINCT seat, state FROM headline_stats WHERE seat ILIKE ? "
            + ("AND state = ? " if state else "")
            + "ORDER BY seat",
            ["%" + seat.strip() + "%"] + ([state] if state else []),
        )
    return [(r["seat"], r["state"]) for r in rows], how


def _groups(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str, str], list[str]] = {}
    for seat, state in pairs:
        name = re.sub(r"^[PN]\.\d+[A-Z]?\s+", "", seat).lower()
        groups.setdefault((name, state, seat[0]), []).append(seat)
    return groups


def _one_seat(lake: Lake, seat: str, state: str | None) -> tuple[list[str], str, dict] | dict:
    pairs, how = _resolve(lake, seat, state)
    if not pairs:
        return {"error": f"No seat matches '{seat}'. Use search to find it."}
    groups = _groups(pairs)
    if len(groups) > 1:
        return {
            "ambiguous": [{"seats": v, "state": k[1]} for k, v in groups.items()],
            "hint": "Pass the full seat name with code, and the state.",
        }
    ((key, seats),) = groups.items()
    match = {"seat_match": how}
    if how == "partial name":
        match["warning"] = f"'{seat}' matched only part of the seat name {', '.join(seats)}. Confirm this is the seat you mean."
    return seats, key[1], match


def search(
    lake: Lake,
    text: str,
    kind: str | None = None,
    state: str | None = None,
    limit: int = 10,
) -> dict:
    check_limit(limit, 100)
    state = _norm_state(lake, state)
    out: dict = {}
    q = text.strip().lower()
    st = "AND state = ?" if state else ""
    sp = [state] if state else []
    if kind in (None, "seat"):
        out["seats"] = lake.records(
            f"""SELECT seat, state, CASE WHEN seat LIKE 'P.%' THEN 'parlimen' ELSE 'dun' END AS type, max(election) FILTER (WHERE election <> 'BY-ELECTION') AS last_election,
                   max(jaro_winkler_similarity({SEAT_NAME}, ?)) + (CASE WHEN lower(seat) LIKE ? THEN 0.5 ELSE 0 END) AS score
                FROM headline_stats WHERE 1=1 {st} GROUP BY seat, state HAVING score > 0.8 ORDER BY score DESC, max(date) DESC LIMIT ?""",
            [q, f"%{q}%", *sp, limit],
        )
    if kind in (None, "candidate"):
        out["candidates"] = lake.records(
            f"""SELECT candidate_uid, any_value(name) AS name, count(*) AS contests, max(date) AS last_contest,
                   max(jaro_winkler_similarity(lower(name), ?)) + (CASE WHEN bool_or(lower(name) LIKE ?) THEN 0.5 ELSE 0 END) AS score
                FROM headline_ballots WHERE 1=1 {st} GROUP BY candidate_uid HAVING score > 0.8 ORDER BY score DESC, contests DESC LIMIT ?""",
            [q, f"%{q}%", *sp, limit],
        )
    if kind in (None, "party"):
        out["parties"] = lake.records(
            f"""SELECT party_uid, party, count(*) AS candidacies, max(date) AS last_contest,
                   max(jaro_winkler_similarity(lower(party), ?)) + (CASE WHEN lower(party) = ? OR lower(party_uid) LIKE ? THEN 0.5 ELSE 0 END) AS score
                FROM headline_ballots WHERE 1=1 {st} GROUP BY party_uid, party HAVING score > 0.85 ORDER BY score DESC, candidacies DESC LIMIT ?""",
            [q, q, f"%{q}%", *sp, limit],
        )
        out["coalitions"] = lake.records(
            f"""SELECT coalition_uid, coalition, count(*) AS candidacies, max(date) AS last_contest
                FROM headline_ballots WHERE lower(coalition) = ? {st} GROUP BY ALL ORDER BY candidacies DESC LIMIT ?""",
            [q, *sp, limit],
        )
    for rows in out.values():
        for r in rows:
            r.pop("score", None)
    out["scope"] = (
        f"Matches for '{text}'" + (f" in {state}" if state else "") + ". " + CREDIT
    )
    return out


WINNER_SQL = """
SELECT s.date, s.election, s.state, s.seat, b.name AS winner, b.candidate_uid, b.party, b.coalition, b.votes_perc AS winner_votes_perc,
       s.majority, s.majority_perc, s.voters_total, s.voter_turnout AS turnout_perc, s.n_candidates,
       b.result = 'won_uncontested' AS uncontested
FROM headline_stats s JOIN headline_ballots b USING (date, election, state, seat)
WHERE b.result IN ('won', 'won_uncontested') AND {where}
ORDER BY s.date DESC, s.seat
"""


def seat_history(lake: Lake, seat: str, state: str | None = None) -> dict:
    found = _one_seat(lake, seat, state)
    if isinstance(found, dict):
        return found
    seats, st, match = found
    rows = lake.records(
        WINNER_SQL.format(where="s.state = ? AND list_contains(?, s.seat)"), [st, seats]
    )
    return warn(match | {
        "scope": f"Every contest for {', '.join(seats)} ({st}), newest first. Seat codes change with boundary reviews; use seat_lineage for boundary changes. {CREDIT}",
        "history": rows,
    }, row_warnings(rows))


def contest_result(
    lake: Lake,
    election: str,
    seat: str,
    state: str | None = None,
    date: str | None = None,
) -> dict:
    found = _one_seat(lake, seat, state)
    if isinstance(found, dict):
        return found
    seats, st, match = found
    date = check_date("date", date)
    where = "election = ? AND state = ? AND list_contains(?, seat)" + (
        " AND date = ?" if date else ""
    )
    params = [election.strip().upper(), st, seats] + ([date] if date else [])
    stats = lake.records(
        f"SELECT * FROM headline_stats WHERE {where} ORDER BY date DESC", params
    )
    if not stats:
        return match | {
            "error": f"No {election} contest for {', '.join(seats)} ({st}). Check seat_history for the elections held there."
        }
    contests = []
    for s in stats:
        ballot = lake.records(
            "SELECT name, candidate_uid, party, party_uid, coalition, coalition_uid, sex, ethnicity, age, votes, votes_perc, rank, result "
            "FROM headline_ballots WHERE date = ? AND election = ? AND state = ? AND seat = ? ORDER BY votes DESC, ballot_order",
            [s["date"], s["election"], s["state"], s["seat"]],
        )
        contests.append({"stats": s, "ballot": ballot})
    held = sorted({s["seat"] for s in stats})
    return warn(match | {
        "scope": f"{election.strip().upper()} result for {', '.join(held)} ({st}). voter_turnout is a percentage. {CREDIT}",
        "contests": contests,
    }, row_warnings(stats))


def candidate_profile(lake: Lake, candidate_uid: str) -> dict:
    candidate_uid = candidate_uid.strip().upper()
    rows = lake.records(
        "SELECT date, election, state, seat, name_on_ballot, party, party_uid, coalition, votes, votes_perc, rank, result "
        "FROM headline_ballots WHERE candidate_uid = ? ORDER BY date DESC",
        [candidate_uid],
    )
    if not rows:
        raise KeyError(
            f"No candidate with uid '{candidate_uid}'. Use search with kind='candidate'."
        )
    name = lake.records(
        "SELECT any_value(name) AS n FROM headline_ballots WHERE candidate_uid = ?",
        [candidate_uid],
    )[0]["n"]
    results = [r["result"] for r in rows]
    return warn({
        "scope": f"Every contest for {name} ({candidate_uid}), newest first. {CREDIT}",
        "name": name,
        "totals": {
            "contests": len(rows),
            "won": sum(r.startswith("won") for r in results),
            "lost": sum(r.startswith("lost") for r in results),
            "lost_deposit": results.count("lost_deposit"),
            "parties": sorted({r["party"] for r in rows if r["party"]}),
        },
        "contests": rows,
    }, row_warnings(rows))


def _summary(lake: Lake, election: str, state: str | None) -> dict:
    scope = "election = ?" + (" AND state = ?" if state else "")
    p = [election] + ([state] if state else [])
    stats = lake.records(
        f"""SELECT count(*) AS contests, count(*) FILTER (WHERE n_candidates = 1) AS uncontested_seats,
               sum(voters_total) FILTER (WHERE n_candidates > 1) AS voters_contested, sum(ballots_issued) FILTER (WHERE n_candidates > 1) AS ballots_issued_contested,
               round(100.0 * sum(ballots_issued) FILTER (WHERE n_candidates > 1) / nullif(sum(voters_total) FILTER (WHERE n_candidates > 1), 0), 2) AS turnout_perc,
               min(date) AS first_date, max(date) AS last_date
            FROM headline_stats WHERE {scope}""",
        p,
    )[0]
    total_votes = "sum(sum(votes)) OVER ()"
    by = {}
    for level, cols in (
        ("by_coalition", "coalition"),
        ("by_party", "party, party_uid, coalition"),
    ):
        by[level] = lake.records(
            f"""SELECT {cols}, count(*) FILTER (WHERE result LIKE 'won%') AS seats_won, count(*) AS candidates, sum(votes) AS votes,
                   round(100.0 * sum(votes) / {total_votes}, 2) AS votes_perc
                FROM headline_ballots WHERE {scope} GROUP BY ALL ORDER BY seats_won DESC, votes DESC""",
            p,
        )
    return {**stats, **by}


def election_summary(lake: Lake, election: str, state: str | None = None) -> dict:
    st = _norm_state(lake, state)
    election = check_election(lake, election, st)
    states = [
        r["state"]
        for r in lake.records(
            "SELECT DISTINCT state FROM headline_stats WHERE election = ? ORDER BY state",
            [election],
        )
    ]
    if st:
        blocks = {st: _summary(lake, election, st)}
    elif election.startswith("SE"):
        blocks = {s: _summary(lake, election, s) for s in states}
    else:
        blocks = {"Malaysia": _summary(lake, election, None)}
    note = (
        "State elections are summarised per state, because each state election is separate."
        if election.startswith("SE") and not st
        else ""
    )
    return warn({
        "scope": f"{election} summary. Turnout excludes uncontested seats. {note} {CREDIT}".replace(
            "  ", " "
        ),
        "by_state": blocks,
    }, data_warnings(election, [st] if st else (None if election.startswith("GE") else states)))


def byelections(
    lake: Lake,
    state: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    st = _norm_state(lake, state)
    date_from = check_date("date_from", date_from)
    date_to = check_date("date_to", date_to)
    where = (
        "s.election = 'BY-ELECTION'"
        + (" AND s.state = ?" if st else "")
        + (" AND s.date >= ?" if date_from else "")
        + (" AND s.date <= ?" if date_to else "")
    )
    params = [x for x in (st, date_from, date_to) if x]
    return {
        "scope": f"By-elections, newest first. {CREDIT}",
        "byelections": lake.records(WINNER_SQL.format(where=where), params),
    }


def seat_demographics(
    lake: Lake, seat: str, state: str | None = None, election: str | None = None
) -> dict:
    found = _one_seat(lake, seat, state)
    if isinstance(found, dict):
        return found
    seats, st, match = found
    params = [st, seats] + ([election.upper()] if election else [])
    rows = lake.records(
        "SELECT * FROM voter_demographics WHERE state = ? AND list_contains(?, seat)"
        + (" AND election = ?" if election else "")
        + " ORDER BY date DESC LIMIT 1",
        params,
    )
    roll_note = ""
    if not rows and election:
        day = lake.records("SELECT min(date) AS d FROM headline_stats WHERE state = ? AND election = ?", [st, election.upper()])[0]["d"]
        if day:
            rows = lake.records(
                "SELECT * FROM voter_demographics WHERE state = ? AND list_contains(?, seat) AND date <= ? ORDER BY date DESC LIMIT 1",
                [st, seats, day],
            )
            if rows:
                roll_note = f" {election.upper()} ({day}) has no roll of its own here; this is the latest roll on or before polling day."
    if not rows:
        return match | {
            "error": f"No demographics for {', '.join(seats)} ({st})"
            + (f" in {election}" if election else "")
            + ". Coverage: GE-12 to GE-15 and some state elections."
        }
    out = {
        "scope": f"Voters by sex, age band, ethnicity and voter type, from the voter roll for {rows[0]['election']} dated {rows[0]['date']}.{roll_note} {CREDIT}",
        "election": rows[0]["election"],
        "demographics": rows[0],
    }
    extra = {
        "Sabah": "voter_demographics_sabah",
        "Sarawak": "voter_demographics_sarawak",
    }.get(st)
    if extra:
        detail = lake.records(
            f"SELECT * FROM {extra} WHERE seat = ? AND date = ?",
            [rows[0]["seat"], rows[0]["date"]],
        )
        if detail:
            out["ethnic_detail"] = detail[0]
    return match | out


def saluran_dataset(lake: Lake, election: str, state: str) -> str | None:
    election = election.upper()
    num = election.split("-")[-1].lstrip("0")
    if election.startswith("GE"):
        name = f"saluran_ballots_ge{num}"
    elif election.startswith("SE") and state in STATE_CODES:
        name = f"saluran_ballots_{STATE_CODES[state]}_se{num}"
    else:
        return None
    return name if name in lake.catalogue else None


def polling_centre_results(
    lake: Lake,
    election: str,
    seat: str,
    state: str | None = None,
    level: str = "saluran",
    limit: int = 2000,
) -> dict:
    if level not in ("saluran", "dm"):
        raise ValueError(
            "level must be 'saluran' (polling stream) or 'dm' (polling district)"
        )
    found = _one_seat(lake, seat, state)
    if isinstance(found, dict):
        return found
    seats, st, match = found
    ballots = saluran_dataset(lake, election, st)
    if not ballots:
        have = sorted(n for n in lake.catalogue if n.startswith("saluran_ballots_"))
        return match | {
            "note": f"There is no polling-centre data for {election.upper()} in {st}. Available: {', '.join(have)}.",
            "rows": [],
        }
    lake.ensure([ballots])
    keys = "dm, pm, saluran" if level == "saluran" else "dm"
    r = lake.query(
        f"""SELECT {keys}, name, party, coalition, sum(votes) AS votes
            FROM {ballots} WHERE state = ? AND list_contains(?, seat) AND election = ?
            GROUP BY ALL ORDER BY {keys}, votes DESC""",
        [st, seats, election.upper()],
        limit=limit,
    )
    rows = [dict(zip(r["columns"], row)) for row in r["rows"]]
    return warn(match | {
        "scope": f"{election.upper()} votes per {'polling stream (saluran)' if level == 'saluran' else 'polling district (dm)'} for {', '.join(seats)} ({st}), from {ballots}. {CREDIT}",
        "rows": rows,
        "truncated": r["truncated"],
        "row_count": r["row_count"],
    }, data_warnings(election, [st], seats))

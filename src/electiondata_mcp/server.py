import functools
import inspect
import json
import logging
import re
import time
from collections import defaultdict, deque
from importlib import resources
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from electiondata_mcp import analysis, geo, lookups
from electiondata_mcp import api as apimod
from electiondata_mcp import catalogue as cat_mod
from electiondata_mcp.api import Api
from electiondata_mcp.lake import Lake

log = logging.getLogger("electiondata_mcp")
SITE = "https://electiondata.my/"
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
STATE_PAGE_CODES = {
    "Johor": "jhr",
    "Kedah": "kdh",
    "Kelantan": "ktn",
    "W.P. Kuala Lumpur": "kul",
    "W.P. Labuan": "lbn",
    "Melaka": "mlk",
    "Malaysia": "mys",
    "Negeri Sembilan": "nsn",
    "Pahang": "phg",
    "W.P. Putrajaya": "pjy",
    "Perlis": "pls",
    "Pulau Pinang": "png",
    "Perak": "prk",
    "Sabah": "sbh",
    "Selangor": "sgr",
    "Sarawak": "swk",
    "Terengganu": "trg",
}
TOOL_SOURCES = {
    "search": ["headline_ballots", "headline_stats"],
    "seat_history": ["headline_ballots", "headline_stats"],
    "contest_result": ["headline_ballots", "headline_stats"],
    "candidate_profile": ["headline_ballots"],
    "election_summary": ["headline_ballots", "headline_stats"],
    "byelections": ["headline_ballots", "headline_stats"],
    "seat_demographics": ["voter_demographics"],
    "swing": ["headline_ballots"],
    "marginal_seats": ["headline_ballots", "headline_stats"],
    "compare": ["headline_ballots"],
    "seat_features": ["headline_ballots", "headline_stats", "voter_demographics"],
}
INSTRUCTIONS = """Malaysian election data from ElectionData.MY (MECo): every federal and state contest since 1955, by-elections,
polling-centre results, voter demographics, anonymised voter rolls, boundary maps, and live turnout on polling day.

How to answer:
1. Names first: call `search` to turn loose text into exact seat names, candidate_uid or party_uid.
2. Common questions: seat_history, contest_result, candidate_profile, election_summary, byelections,
   seat_demographics, polling_centre_results, find_seat_by_location.
3. Analysis: swing, marginal_seats, compare, seat_features. For anything else: search_datasets,
   describe_dataset, join_guide, then run_sql (DuckDB SQL, read only). Read electiondata://guide first.
4. Polling day: live_turnout (provisional). Boundary changes: seat_lineage. Party name history: party_history.
5. Full data on the user's machine: data_access.

Accuracy rules: quote numbers exactly as returned; say which election, state and seat they belong to;
pass on any `warning`, `data_warnings`, `ambiguous` or `truncated` field; never fill gaps with guesses.
`data_warnings` lists known differences between this data and the official SPR gazette; give the gazette figure.
Each result lists its `sources` and dashboard `links` so a person can check them.
Credit: Data from ElectionData.MY (MECo, Thevananthan 2025, doi:10.1038/s41597-025-06502-7), CC0. Official source: SPR."""


def _message(e: Exception) -> str:
    return str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)


def _row_count(result) -> int | None:
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("row_count"), int):
        return result["row_count"]
    for v in result.values():
        if isinstance(v, list):
            return len(v)
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace(".", "")).strip("-")


class Links:
    def __init__(self, pages: dict[str, list[str]]):
        self.pages = {k: set(v) for k, v in pages.items()}

    def seat(self, seat: str, state: str) -> str | None:
        kind = "parlimen" if seat.startswith("P.") else "dun"
        path = f"seats/{kind}/{_slug(seat)}-{_slug(state)}/"
        return SITE + path if path in self.pages.get("seat", ()) else None

    def candidate(self, uid: str) -> str | None:
        path = f"candidates/{uid}/"
        return SITE + path if path in self.pages.get("candidate", ()) else None

    def election(self, election: str, state: str) -> str | None:
        path = f"elections/{STATE_PAGE_CODES.get(state, '')}/{election}/"
        return SITE + path if path in self.pages.get("election", ()) else None

    def collect(self, result: dict) -> list[str]:
        found: list[str] = []

        def add(u):
            if u and u not in found and len(found) < 25:
                found.append(u)

        def walk(o):
            if isinstance(o, dict):
                if isinstance(o.get("seat"), str) and isinstance(o.get("state"), str):
                    add(self.seat(o["seat"], o["state"]))
                if isinstance(o.get("candidate_uid"), str):
                    add(self.candidate(o["candidate_uid"]))
                if isinstance(o.get("election"), str) and isinstance(
                    o.get("state"), str
                ):
                    add(self.election(o["election"], o["state"]))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(result)
        return found


def build_server(
    lake: Lake, api: Api, catalogue: list[dict], pages: dict[str, list[str]]
) -> MCPServer:
    server = MCPServer(
        name="electiondata-mcp",
        title="ElectionData.MY",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/azwanzuharimi/electiondata_mcp",
    )
    links = Links(pages)
    by_name = {e["name"]: e for e in catalogue}

    def sources(names: list[str]) -> list[dict]:
        out = []
        for n in names:
            e = by_name.get(n)
            if e:
                out.append(
                    {
                        "dataset": n,
                        "parquet": e["parquet"],
                        "catalogue_page": f"{SITE}data-catalogue/{e['slug']}"
                        if e.get("slug")
                        else None,
                        "data_as_of": e.get("data_as_of"),
                    }
                )
        return out

    def finish(name: str, result, extra_sources: list[str] = ()):
        if isinstance(result, dict) and result.get("error"):
            raise ValueError(result["error"])
        if isinstance(result, dict):
            used = TOOL_SOURCES.get(name, []) + list(extra_sources)
            if used:
                result.setdefault("sources", sources(used))
            found = links.collect(result)
            if found:
                result.setdefault("links", found)
        return result

    def tool(description: str):
        def deco(fn):
            name = fn.__name__
            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def wrapper(*a, **kw):
                    started = time.perf_counter()
                    err = rows = None
                    try:
                        out = finish(name, await fn(*a, **kw))
                        rows = _row_count(out)
                        return out
                    except Exception as e:
                        err = type(e).__name__
                        raise ToolError(_message(e)) from e
                    finally:
                        log.info(
                            json.dumps(
                                {
                                    "tool": name,
                                    "ms": round((time.perf_counter() - started) * 1000),
                                    "rows": rows,
                                    "error": err,
                                }
                            )
                        )
            else:

                @functools.wraps(fn)
                def wrapper(*a, **kw):
                    started = time.perf_counter()
                    err = rows = None
                    try:
                        out = finish(name, fn(*a, **kw))
                        rows = _row_count(out)
                        return out
                    except Exception as e:
                        err = type(e).__name__
                        raise ToolError(_message(e)) from e
                    finally:
                        log.info(
                            json.dumps(
                                {
                                    "tool": name,
                                    "ms": round((time.perf_counter() - started) * 1000),
                                    "rows": rows,
                                    "error": err,
                                }
                            )
                        )

            return server.tool(
                name=name, description=description, annotations=READ_ONLY, structured_output=True
            )(wrapper)

        return deco

    @tool(
        "Find exact seat names, candidate_uid, party_uid or coalitions from loose text. Call this first when the user names a seat, person or party."
    )
    def search(
        text: str,
        kind: Literal["seat", "candidate", "party"] | None = None,
        state: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return lookups.search(lake, text, kind, state, limit)

    @tool(
        "Every contest held in a seat, newest first: winner, party, coalition, majority, turnout. Seat codes change over time; the history follows the seat name."
    )
    def seat_history(seat: str, state: str | None = None) -> dict[str, Any]:
        return lookups.seat_history(lake, seat, state)

    @tool(
        "Full result of one contest: every candidate's votes, share and result, plus contest stats (voters, turnout, rejected votes, majority)."
    )
    def contest_result(
        election: str, seat: str, state: str | None = None, date: str | None = None
    ) -> dict[str, Any]:
        return lookups.contest_result(lake, election, seat, state, date)

    @tool(
        "Every contest a candidate stood in, with party at the time and result, plus totals. Get candidate_uid from search."
    )
    def candidate_profile(candidate_uid: str) -> dict[str, Any]:
        return lookups.candidate_profile(lake, candidate_uid)

    @tool(
        "Seats won and votes by coalition and party, turnout and uncontested seats for one election. GE-xx = Malaysia unless a state is given; SE-xx = one block per state."
    )
    def election_summary(election: str, state: str | None = None) -> dict[str, Any]:
        return lookups.election_summary(lake, election, state)

    @tool(
        "All by-elections with winner, majority and turnout, newest first. Dates as YYYY-MM-DD."
    )
    def byelections(
        state: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        return lookups.byelections(lake, state, date_from, date_to)

    @tool(
        "Voters in a seat by sex, age band, ethnicity and voter type, from the voter roll of an election (latest if not given)."
    )
    def seat_demographics(
        seat: str, state: str | None = None, election: str | None = None
    ) -> dict[str, Any]:
        return lookups.seat_demographics(lake, seat, state, election)

    @tool(
        "Votes per polling stream (saluran) or polling district (dm) for one seat and election. Available for GE-12 to GE-15 and many state elections."
    )
    def polling_centre_results(
        election: str,
        seat: str,
        state: str | None = None,
        level: Literal["saluran", "dm"] = "saluran",
    ) -> dict[str, Any]:
        r = lookups.polling_centre_results(lake, election, seat, state, level)
        ds = re.search(r"from (saluran_ballots_\w+)", r.get("scope", ""))
        return finish("polling_centre_results", r, [ds.group(1)] if ds else [])

    @tool(
        "Vote share per seat for one coalition or party in two elections, and the change. Seats whose name changed are listed apart."
    )
    def swing(
        election_from: str,
        election_to: str,
        group: str,
        state: str | None = None,
        by: Literal["coalition", "party"] = "coalition",
    ) -> dict[str, Any]:
        return analysis.swing(lake, election_from, election_to, group, state, by)

    @tool(
        "Closest contests in an election, smallest majority first, with winner and runner-up."
    )
    def marginal_seats(
        election: str,
        state: str | None = None,
        max_majority_perc: float | None = None,
        max_majority: int | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        return analysis.marginal_seats(
            lake, election, state, max_majority_perc, max_majority, limit
        )

    @tool(
        "Compare 2 to 5 candidates (by candidate_uid), parties or coalitions (by acronym or uid) side by side across elections."
    )
    def compare(
        kind: Literal["candidate", "party", "coalition"], ids: list[str]
    ) -> dict[str, Any]:
        return analysis.compare(lake, kind, ids)

    @tool(
        "One row per seat for an election: turnout, majority, winner, vote share per coalition, previous result and voter demographics. Ready for modelling."
    )
    def seat_features(
        election: str,
        state: str | None = None,
        level: Literal["parlimen", "dun"] = "parlimen",
    ) -> dict[str, Any]:
        return analysis.seat_features(lake, election, state, level)

    @tool(
        "Find the Parliament and state seats (optionally polling district) that contain a point. Uses the boundary maps in force in the given year (now if not given)."
    )
    def find_seat_by_location(
        lat: float,
        lon: float,
        year: int | None = None,
        include_district: bool = False,
        include_proposed: bool = False,
    ) -> dict[str, Any]:
        r = geo.find_seat_by_location(lake, lat, lon, year, include_district, include_proposed)
        return finish("find_seat_by_location", r, sorted({m["map"] for m in r["matches"]}))

    @tool(
        "Search all 208 lake datasets by topic, column, election or state. Use before run_sql to find table names."
    )
    def search_datasets(query: str, group: str | None = None, limit: int = 20) -> dict[str, Any]:
        return {"datasets": cat_mod.search_datasets(catalogue, query, group, limit)}

    @tool("Columns, coverage, join keys and download links of one dataset.")
    def describe_dataset(name: str) -> dict[str, Any]:
        return cat_mod.describe_dataset(catalogue, name)

    @tool("How to join two or more datasets: the columns to join on and known traps.")
    def join_guide(datasets: list[str]) -> dict[str, Any]:
        return cat_mod.join_guide(catalogue, datasets)

    @tool(
        "Links to electiondata.my dashboard pages (English and Bahasa Malaysia) for a seat, candidate, party, coalition, election or boundary map."
    )
    def find_dashboard(
        kind: Literal[
            "seat", "candidate", "party", "coalition", "election", "redelineation"
        ],
        text: str,
    ) -> dict[str, Any]:
        return {"pages": cat_mod.find_dashboard(pages, kind, text)}

    @tool(
        "Direct Parquet links and ready code (Python, R) to load full datasets on the user's own machine, with no row limit. Good for machine learning."
    )
    def data_access(datasets: list[str] | None = None, sql: str | None = None) -> dict[str, Any]:
        return cat_mod.data_access(catalogue, datasets, sql)

    @tool(
        "Run one read-only DuckDB SELECT on the lake tables. Default 200 rows, max 2000; truncated=true means more rows exist. Voter roll tables need LIMIT <= 10000 or an aggregate."
    )
    def run_sql(sql: str, limit: int = 200) -> dict[str, Any]:
        r = lake.run_sql(sql, limit)
        used = [n for n in by_name if re.search(rf"\b{re.escape(n)}\b", sql)]
        notes = [
            "Applies if your rows include this seat: " + k["note"]
            for k in lookups.KNOWN_ISSUES
            if set(k["tables"]) & set(used)
        ]
        return finish("run_sql", lookups.warn(r, notes), used)

    @tool(
        "PROVISIONAL live turnout on polling day (SPR feed, every 5 minutes, 08:00-18:00 MYT). election like 'mlk-se16'. Optional level, HHMM slot, or seat filter."
    )
    async def live_turnout(
        election: str,
        level: Literal["parlimen", "dun", "dm"] | None = None,
        time: str | None = None,
        seat: str | None = None,
    ) -> dict[str, Any]:
        r = await apimod.live_turnout(api, election, level, time, seat)
        r.setdefault("sources", [{"api": apimod.BASE + "/vto", "provider": "ElectionData.MY API"}])
        return r

    @tool(
        "A party's or coalition's seats and votes over time in one state, with old names merged (e.g. PERIKATAN into BN)."
    )
    async def party_history(
        party: str,
        state: str = "Malaysia",
        election_type: Literal["parlimen", "dun"] = "parlimen",
    ) -> dict[str, Any]:
        r = await apimod.party_history(api, party, state, election_type)
        r.setdefault("sources", [{"api": apimod.BASE + "/parties", "provider": "ElectionData.MY API"}])
        return r

    @tool(
        "A seat's results back through boundary changes, with notes on each change (from the ElectionData.MY API)."
    )
    async def seat_lineage(seat: str, state: str | None = None) -> dict[str, Any]:
        r = await apimod.seat_lineage(api, seat, state)
        r.setdefault("sources", [{"api": apimod.BASE + "/seats", "provider": "ElectionData.MY API"}])
        return r

    @server.resource(
        "electiondata://guide",
        name="guide",
        description="Names, definitions, joins, traps and worked SQL. Read before run_sql.",
        mime_type="text/markdown",
    )
    def guide() -> str:
        return (
            resources.files("electiondata_mcp")
            .joinpath("guide.md")
            .read_text(encoding="utf-8")
        )

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return server


class RateLimit:
    def __init__(self, app, per_minute: int, trust_proxy: bool = False, max_clients: int = 10_000):
        self.app = app
        self.per_minute = per_minute
        self.trust_proxy = trust_proxy
        self.max_clients = max_clients
        self.hits: dict[str, deque] = defaultdict(deque)

    def client_ip(self, scope) -> str:
        if self.trust_proxy:
            fwd = dict(scope.get("headers") or []).get(b"x-forwarded-for", b"").decode()
            if fwd.strip():
                return fwd.split(",")[-1].strip()
        return (scope.get("client") or ("unknown",))[0]

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "POST" and self.per_minute > 0:
            now = time.monotonic()
            if len(self.hits) > self.max_clients:
                for key in [k for k, q in self.hits.items() if not q or now - q[-1] > 60]:
                    del self.hits[key]
            q = self.hits[self.client_ip(scope)]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                await JSONResponse({"error": f"Rate limit: {self.per_minute} requests per minute. Try again shortly."}, status_code=429)(scope, receive, send)
                return
            q.append(now)
        await self.app(scope, receive, send)

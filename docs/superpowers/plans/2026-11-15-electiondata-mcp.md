# electiondata_mcp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted MCP server that answers questions about Malaysian elections from the ElectionData.MY lake and API.

**Architecture:** One Python process. DuckDB in memory holds the small lake tables and views over the large ones, locked so SQL can read only the lake. Tools are plain Python functions over that database (and an API client for live turnout, lineage and party names), registered on an `MCPServer` that runs over stdio or Streamable HTTP.

**Tech Stack:** Python 3.12, uv, `mcp` 2.3 (`mcp.server.mcpserver.MCPServer`), `duckdb` 2.0 with `httpfs`, `httpx2` (comes with `mcp`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-15-electiondata-mcp-design.md`

## Global Constraints

- No new runtime dependency beyond `mcp` and `duckdb`. No `shapely`, no `spatial` extension.
- SQL safety settings exactly as spec 7.6; lake prefix `https://lake.electiondata.my/`.
- `run_sql`: one `SELECT`/`WITH` statement; default 200 rows, max 2,000; 30 s time limit; voter roll queries need `LIMIT` ≤ 10,000 or an aggregate.
- API key from env `ELECTIONDATA_API_KEY`; never logged or returned. Without it, API tools return a "not configured" message.
- API cache: 300 s, live turnout 30 s; identical requests in flight share one call.
- Rate limit: 60 tool calls per minute per client IP, env `RATE_LIMIT_PER_MIN`.
- Memory limit env `DUCKDB_MEMORY_LIMIT`, default `1GB`.
- Every tool result states its scope and any truncation.
- Credit line in server instructions and in `electiondata://guide`.
- Table names: the 30 Query Builder names exactly as in `datasets.ts`.
- No code comments unless a shortcut needs one (`shortcut: <limit>, <when to upgrade>`).

## Review Focus

1. Seat names typed loosely ("lembah pantai", "Kapar") must still resolve; tools take `seat` text and match case-insensitively on the name without the `P.`/`N.` code. Test in Task 4.
2. SE-16 or any election name that exists in several states: tools that take `election` without `state` must return results per state, never a silent mix. Test in Task 4.
3. Uncontested seats (zero votes, null percentages) must not break sums or swing. Test in Task 5.
4. A point outside every seat (sea) returns "no seat found", not an error. Test in Task 6.
5. API down or no key: lake tools keep working; API tools return a clear message. Test in Task 7.

---

## File map

```
pyproject.toml
src/electiondata_mcp/
  __init__.py
  __main__.py          CLI entry: stdio or http
  config.py            env settings
  catalogue.json       generated dataset list (Task 1)
  pages.json           generated dashboard index (Task 1)
  catalogue.py         load catalogue, search, describe, join map, data access code
  lake.py              DuckDB setup, sandbox, run_sql, refresh
  sqlguard.py          SQL statement checks
  lookups.py           seat/contest/candidate/election/by-election/demographics/polling-centre
  analysis.py          swing, marginal seats, compare, seat features
  geo.py               point-in-seat
  api.py               API client with cache
  guide.md             guide resource text
  server.py            MCPServer, tool registration, health, rate limit, logs
scripts/build_catalogue.py
tests/conftest.py      fixture lake (local Parquet copies)
tests/data/            small Parquet extracts
tests/test_*.py
Dockerfile, .dockerignore, README.md
```

### Task 1: Project, catalogue builder, generated indexes

**Files:** Create `pyproject.toml`, `scripts/build_catalogue.py`, `src/electiondata_mcp/catalogue.json`, `src/electiondata_mcp/pages.json`, `tests/test_catalogue_files.py`

**Interfaces:**
- Produces `catalogue.json`: list of `{name, slug, title, description, group, parquet, csv, rows, size_bytes, columns: [{name, title, description}], streams: bool}`. `name` = Query Builder name when the URL matches `datasets.ts`, else derived from the Parquet path: `results_saluran/sgr_se15_ballots.parquet` → `saluran_ballots_sgr_se15`, `voter_rolls/kdh_se15_2023.parquet` → `voter_roll_kdh_se15`, `maps/delimitations/peninsular_2018_parlimen.parquet` → `map_peninsular_2018_parlimen`, `results_headline/headline_ballots_state_sgr.parquet` → `headline_ballots_state_sgr`. `streams` = true for voter rolls and maps.
- Produces `pages.json`: `{kind: [path,...]}` for kinds seat, candidate, party, coalition, election, redelineation, from `sitemap-0.xml`, English paths only.

- [ ] Step 1: `uv init --package`, add `mcp==2.3.*`, `duckdb==2.0.*`, dev `pytest`, `pytest-asyncio`.
- [ ] Step 2: test `test_catalogue_has_all_datasets`: ≥ 200 entries; all 30 Query Builder names present; names unique; every entry has a `parquet` URL under the lake prefix or is a map/geo entry with one.
- [ ] Step 3: test `test_pages_index`: `len(pages["candidate"]) > 10000`, `len(pages["seat"]) > 800`.
- [ ] Step 4: write the script. Catalogue pages embed Astro props as HTML-escaped JSON in the form `[0, value]`/`[1, [...]]`; unescape and unwrap. Query Builder names come from `datasets.ts` on GitHub raw. Run it; commit generated JSON.
- [ ] Step 5: tests pass; commit.

### Task 2: Lake database, SQL guard, `run_sql`

**Files:** Create `config.py`, `lake.py`, `sqlguard.py`, `tests/conftest.py`, `tests/data/*.parquet`, `tests/test_lake.py`

**Interfaces:**
- `class Lake(catalogue: list[dict], url_map: Callable[[str], str] = identity, memory_limit="1GB", timeout_s=30, extra_allowed_dirs: list[str] = [])`
  - `.con` locked DuckDB connection; tables created for in-memory datasets (`headline_ballots`, `headline_stats`, `voter_demographics`, `voter_demographics_sabah`, `voter_demographics_sarawak`), views for all others.
  - `.query(sql: str, params: list | None = None, limit: int = 200) -> dict` → `{columns, rows, row_count, truncated, elapsed_ms}`; internal, no guard.
  - `.run_sql(sql: str, limit: int = 200) -> dict` → guard + `query`; `limit` clamped to 2,000.
  - `.refresh_if_stale()` → re-check `ETag` of in-memory files when last check > 600 s; reload changed ones.
- `sqlguard.check(sql: str, voter_roll_tables: set[str]) -> None` raises `ValueError` with a plain message.
- Fixtures: `tests/data/` holds extracts (GE-14, GE-15, SE-15 Selangor, one by-election set, `saluran_ballots_ge15`/`saluran_stats_ge15` for P.121, demographics, `voter_roll_ge15` first 2,000 rows of P.121, `map_peninsular_2018_parlimen` for Kuala Lumpur and Selangor). `conftest.lake` builds `Lake` with `url_map` to local paths and `allowed_directories` including `tests/data/`.

- [ ] Step 1: tests: lake read passes; each spec 7.6 blocked case raises (other host, `/etc/passwd`, `COPY TO`, `SET`, `INSTALL`, `ATTACH`, two statements, `DELETE`); voter roll without limit raises; `limit=5` returns `truncated: True` and full `row_count`; `limit=99999` clamps to 2,000; a query sleeping past the time limit raises a time-limit error (build the test `Lake` with `timeout_s=1`).
- [ ] Step 2: write a fixture extraction script `tests/make_fixtures.py` (run once, committed with output).
- [ ] Step 3: implement. Guard parses with `json_serialize_sql`; reject `error: true` or more than one statement or a non-`SELECT_NODE` root. Time limit by `threading.Timer` calling `cursor.interrupt()`. Each call uses `con.cursor()`.
- [ ] Step 4: tests pass; commit.

### Task 3: Discovery tools

**Files:** Modify `catalogue.py`; Create `tests/test_discovery.py`

**Interfaces:**
- `search_datasets(cat, query: str, group: str | None = None, limit=20) -> list[dict]` with `{name, title, group, rows, streams, matched_on}`; ranks title > name > column > description hits; all words must match somewhere.
- `describe_dataset(cat, name) -> dict` with columns, links (`catalogue_page`, `parquet`, `csv`), `join_keys`.
- `join_guide(names: list[str]) -> dict` with `{pairs: [{a, b, on, note}], traps: [...]}` from a static join map (spec 7.5, all 4 traps).
- `find_dashboard(pages, kind, text) -> list[dict]` → `{url, url_ms}`; matches on path text, case-insensitive, hyphens = spaces.
- `data_access(cat, names: list[str] | None, sql: str | None) -> dict` → Parquet URLs and code samples for Python DuckDB, pandas, R.

- [ ] Step 1: tests: `search_datasets("selangor polling centre 2018")` includes `saluran_ballots_sgr_se14`; `search_datasets("ethnicity")` includes `voter_demographics`; `describe_dataset("nope")` raises `KeyError` with close-name suggestions; `join_guide(["voter_demographics","headline_stats"])` contains the GE-15 822/600 trap; `find_dashboard(..., "seat", "lembah pantai")` returns a `seats/parlimen/` URL; `data_access(names=["headline_stats"])` code contains the Parquet URL.
- [ ] Step 2: implement; tests pass; commit.

### Task 4: Lookup tools

**Files:** Create `lookups.py`, `tests/test_lookups.py`

**Interfaces** (all take `lake: Lake`, return `dict` with `scope` text and data):
- `search(lake, text, kind=None, state=None, limit=10)` → seats `{seat, state, type}`, candidates `{candidate_uid, name, contests}`, parties `{party_uid, party}`. Score `jaro_winkler_similarity(lower(a), lower(b))` plus `ILIKE` hit.
- `resolve_seat(lake, seat, state=None) -> list[tuple[str, str]]` exact `(seat, state)` pairs; matches full name, or name without the code, case-insensitive.
- `seat_history(lake, seat, state=None)`, `contest_result(lake, election, seat, state=None, date=None)`, `candidate_profile(lake, candidate_uid)`, `election_summary(lake, election, state="Malaysia")`, `byelections(lake, state=None, date_from=None, date_to=None)`, `seat_demographics(lake, seat, state=None, election=None)`, `polling_centre_results(lake, election, seat, state=None, level="saluran")`.
- When a seat text matches several seats, return `{ambiguous: [...]}` instead of guessing.

- [ ] Step 1: tests: `resolve_seat("lembah pantai")` → `[("P.121 Lembah Pantai","W.P. Kuala Lumpur")]` (state as in data); `contest_result("GE-15","P.121 Lembah Pantai")` winner equals the value read independently from `headline_ballots` with `rank = 1` via raw DuckDB in the test; `election_summary("SE-15")` without state returns per-state blocks; `polling_centre_results` for an election with no saluran file says so; `candidate_profile` unknown uid raises `KeyError`.
- [ ] Step 2: implement with parameterised SQL; tests pass; commit.

### Task 5: Analysis tools

**Files:** Create `analysis.py`, `tests/test_analysis.py`

**Interfaces:**
- `swing(lake, election_from, election_to, group: str, state="Malaysia", by="coalition")` → `{rows: [{seat, state, share_from, share_to, change}], unmatched_seats: [...]}`.
- `marginal_seats(lake, election, state="Malaysia", max_majority_perc=None, max_majority=None, limit=30)`.
- `compare(lake, kind: "candidate"|"party"|"coalition", ids: list[str])` → per id: contests, wins, losses, lost deposits, share by election.
- `seat_features(lake, election, state="Malaysia", level="parlimen", limit=2000)` → one row per seat; previous result by same seat name; `name_changed: bool`.

- [ ] Step 1: tests: swing GE-14→GE-15 for PH in Selangor returns rows and lists seats missing in one election under `unmatched_seats`; uncontested seat in fixture does not crash and has null share; `marginal_seats` sorted ascending by majority; `seat_features` row count equals GE-15 Parliament seats in fixture scope.
- [ ] Step 2: implement; tests pass; commit.

### Task 6: Point-in-seat

**Files:** Create `geo.py`, `tests/test_geo.py`

**Interfaces:** `find_seat_by_location(lake, lat: float, lon: float, year: int | None = None) -> dict` → `{map, matches: [{level, seat, state}]}`; picks per region the newest map on or before `year` (default newest); checks Parliament, DUN and DM map sets of that year; `contains(geojson: dict, x, y) -> bool`.

- [ ] Step 1: tests: `contains` on a square with a hole (inside, in hole, outside); KL point 3.1390, 101.6869 → `P.121 Lembah Pantai`; sea point 2.5, 100.5 → no matches with a "no seat found" note.
- [ ] Step 2: implement (bounding box with `st_intersects_extent`, exact test in Python, shapes cached per map); tests pass; commit.

### Task 7: API client and API tools

**Files:** Create `api.py`, `tests/test_api.py`

**Interfaces:**
- `class Api(key: str | None, base="https://api.electiondata.my/v1", transport=None)`; `async get(path, params, ttl) -> dict`; cache by `(path, sorted params)`; in-flight calls shared via `asyncio.Future`.
- Tools: `live_turnout(api, election, level=None, time=None, seat=None)`, `party_history(api, party, state="Malaysia", type="parlimen")` (resolves `maps_to` via `/parties/dropdown`), `seat_lineage(api, seat, state)` (slug via `/seats/dropdown`, then `/seats/results?lineage=true`).
- No key → every API tool returns `{error: "API not configured: set ELECTIONDATA_API_KEY"}`.

- [ ] Step 1: tests with `httpx2.MockTransport`: two concurrent identical calls → one HTTP call; second call within ttl → cached; 401 → plain error; no key → not-configured message; `party_history("PERIKATAN")` queries uid `001-BN`.
- [ ] Step 2: implement; tests pass; commit.

### Task 8: Server wiring

**Files:** Create `server.py`, `__main__.py`, `guide.md`, `tests/test_server.py`

**Interfaces:** `build_server(lake, api, cat, pages) -> MCPServer`; `main()` reads `--stdio` | `--http --host --port`.
- Register every tool from Tasks 2–7 with short descriptions that say when to use it; `search` and `search_datasets` descriptions tell the model to call them first.
- Resource `electiondata://guide` from `guide.md` (naming, join map, traps, 3 SQL examples, credit).
- Instructions: one paragraph on which tool to use when, plus the credit line.
- `GET /health` → `{"ok": true}`.
- Rate limit: Starlette middleware on POST `/mcp`, sliding window per client IP (`X-Forwarded-For` first value if present). `shortcut: counts HTTP requests, not tool calls, upgrade if batching appears`.
- Log line per tool call: name, ms, rows, error type.

- [ ] Step 1: tests with `mcp.Client(server)` in-process: list tools contains all names; `call_tool("contest_result", ...)` returns data; `read_resource("electiondata://guide")` contains "doi:10.1038/s41597-025-06502-7"; rate limit returns 429 on the 61st request in a minute (test the middleware directly with Starlette `TestClient`).
- [ ] Step 2: implement; tests pass; commit.

### Task 9: Docker, README, end-to-end

**Files:** Create `Dockerfile`, `.dockerignore`; Modify `README.md`

- [ ] Step 1: Dockerfile from the measured minimal build (spec 7.9), entry `python -m electiondata_mcp --http --host 0.0.0.0 --port 8000`, `httpfs` installed at build time.
- [ ] Step 2: build; `docker run` with no key; `curl /health` → ok; call `contest_result` over HTTP with `mcp.Client("http://localhost:8000/mcp")` → P.121 GE-15 winner.
- [ ] Step 3: `claude mcp add` with stdio in a temp scope, list tools.
- [ ] Step 4: README sections per spec 7.11, every command run once.
- [ ] Step 5: commit, push.

### Task 10: Review

- [ ] Fresh fable reviewer on the whole branch against the spec; fix confirmed findings; full test suite; push.

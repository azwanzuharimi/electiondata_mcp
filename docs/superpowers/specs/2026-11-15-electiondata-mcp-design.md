# electiondata_mcp: design

Date: 2026-11-15
Status: draft, waiting for approval

## 1. Intent

Build an MCP server that lets a web chat analyse Malaysian election data from
[ElectionData.MY](https://electiondata.my/).

- **Who uses it:** people who ask election questions in a web chat.
- **Two clients, one server:**
  1. Existing chat apps (claude.ai, ChatGPT) that add the server as a connector.
  2. A chat website that a friend of the owner will build later. It calls the same server.
- **Why now:** the next general election (GE16) is coming. Question volume will peak in
  that season, and on polling day most of all.
- **Why a new server:** an MCP for this site already exists
  ([wanadzhar913/electiondata-my-mcp](https://github.com/wanadzhar913/electiondata-my-mcp),
  v0.3.0). It gives the model a SQL tool over the 30 tables of the site's Query Builder.
  This server must cover more: all 205 catalogue datasets, the API, live turnout, and
  boundary maps, plus ready-made lookup tools so the model does not have to write SQL for
  common questions.

## 2. Goals

1. Answer common questions with one tool call each: a seat's history, one contest's full
   result, a candidate's career, an election summary by party, by-elections, seat
   demographics, polling-centre results.
2. Let the model run its own read-only SQL for deeper analysis, over every lake dataset.
3. Give live turnout on polling day from the API.
4. Give seat lineage (boundary changes and ancestor seats) and party name history from the API.
5. Find the seat that contains a map point (latitude and longitude), for any boundary year
   in the lake.
6. Stay light: small container image, low memory, fast start.
7. Run in two connection modes: web (Streamable HTTP) for hosted use, and local (stdio) for
   testing in Claude Code.

## 3. Non-goals

- No chat user interface. The friend builds that.
- No copy of the data. The lake stays the only source.
- No write access of any kind to the data.
- No PyPI package for now. Distribution is the Docker image and the repo.
- No map drawing. The server returns seat names and codes, not images or GeoJSON shapes.

## 4. Anti-goals (failure even if it works)

- The model gives a wrong number because a tool hid a filter, a truncation, or an
  uncontested seat. Every tool result must state its scope and any truncation.
- A user reaches data or systems outside the lake through the SQL tool.
- The server leaks the API key, or someone uses our server to flood the API.
- The server falls over on polling day because of load.

## 5. What the data looks like (measured 2026-11-15)

### 5.1 Data lake: `https://lake.electiondata.my`

Public, no key, CC0. Parquet over HTTPS with range requests and open CORS.

| Group | Files | Size | Notes |
|---|---|---|---|
| Headline results (`results_headline/`) | `headline_ballots`, `headline_stats`, plus per-state and federal splits | ~1.5 MB for the two main files | One row per candidate per contest, and one row per contest. 1955-07-25 to 2026-11-14 (SE-16). |
| Seat demographics (`seat_info/`) | `demographics`, `demographics_sabah`, `demographics_sarawak` | ~0.35 MB | Voters by sex, age band, ethnicity, voter type, per seat per election. |
| Polling-centre results (`results_saluran/`) | 56 ballots + 56 stats files in the catalogue | ~0.05–1.3 MB each | GE-12 to GE-15, and state elections in 13 states (Sabah from SE-11, the rest from SE-12). Columns add `dm`, `pm`, `saluran`. |
| Voter rolls (`voter_rolls/`) | 18 in the catalogue | 5–158 MB each, ~509 MB for the 9 in the Query Builder | One row per voter, anonymised: birth year, sex, ethnicity, seat, polling district. |
| Boundary maps (`maps/delimitations/`) | 43 (Peninsular, Sabah, Sarawak; Parliament, DUN, polling district) | varies | GeoParquet with a native DuckDB `GEOMETRY` column, WGS 84. Also `.geojson`, `.fgb`, `.kml`, `.topojson`. |

Key columns, `headline_ballots`: `date, election, state, seat, ballot_order, candidate_uid,
name_on_ballot, name, sex, ethnicity, age, party_on_ballot, party_uid, party, coalition_uid,
coalition, votes, votes_perc, rank, result`.

Key columns, `headline_stats`: `date, election, state, seat, voters_total, ballots_issued,
ballots_not_returned, votes_rejected, votes_valid, majority, n_candidates, voter_turnout,
majority_perc, votes_rejected_perc, ballots_not_returned_perc`.

The lake has no index file. The dataset list comes from the site's Data Catalogue pages,
one page per dataset at `https://electiondata.my/data-catalogue/<slug>`. Each page embeds
the file links, row count, column count, size, description, and column descriptions.
The 30 Query Builder table names are in the site's source:
`electiondata-my/meco-front/src/components/tools/query-builder/datasets.ts`.

### 5.2 Open API: `https://api.electiondata.my/v1`

Needs a free key (email sign-in at the API Console). Bearer token. No rate limit. CC0.
Responses are cached for 300 s, live turnout for 30 s.

| Endpoint | Gives | In the lake too? |
|---|---|---|
| `/seats/dropdown`, `/seats/results?slug=&lineage=true` | Seat history, with ancestor results and boundary-change events | Lineage: **no** |
| `/parties/dropdown`, `/parties/results` | Party and coalition time series; `maps_to` resolves old names to the current UID (e.g. `001-PERIKATAN` → `001-BN`) | Name mapping: **no** |
| `/candidates/dropdown`, `/candidates?uid=` | Candidate career | Yes |
| `/elections/dropdown`, `/by_party`, `/by_seat`, `/stats` | Election summaries | Yes (computable) |
| `/byelections` | All by-elections | Yes |
| `/results?seat=&state=&election=` | One contest | Yes |
| `/vto/latest?election=`, `/vto?election=&level=&time=` | Live turnout every 5 min, 08:00–18:00 MYT, by state, Parliament seat, DUN seat, polling district | **No** |

Lineage and party name mapping come from a private build bucket of the site, so for this
server they are API-only.

## 6. Approaches considered

| | Approach | Trade-off | Decision |
|---|---|---|---|
| A | Wrap the API: one tool per endpoint | Stable responses, little code. No analysis across elections. Every call needs the key. | Rejected: cannot analyse. |
| B | Lake only: DuckDB over the Parquet files, lookup tools plus a SQL tool | Full analysis, no key. No live turnout, no lineage, no party name mapping. | Rejected: loses polling day. |
| C | Lake first, API for what only the API has | Full analysis plus polling day. Two sources to keep working; one API key on the server. | **Chosen by the owner.** |

Language, also decided by the owner after measurement on 2026-11-15 (same task in each:
load DuckDB, read one lake file, total the votes):

| | Python | Go | Rust |
|---|---|---|---|
| Memory, warm | 70 MB | 58 MB | 49 MB |
| Query time, warm | 0.28–0.37 s | 0.31–0.50 s | 0.42–0.44 s |
| Image, download / unpacked | 75 / 239 MB (minimal build) | ~46 / ~144 MB | not built |
| Build time | none | 6 s | 87 s |

**Python chosen.** The gap to Go is about 30 MB of download and 12 MB of memory. Python is
the owner's daily language, so the owner can fix the server during election season.

## 7. Design

### 7.1 Stack

- Python 3.12, managed by uv.
- Official MCP Python SDK (`mcp`, v2.x), FastMCP style.
- DuckDB 2.x with the `httpfs` extension. One in-process database.
- `httpx2` (already a dependency of the SDK) for API calls.
- No geometry library. The point-in-seat lookup uses DuckDB core plus a short Python test (see 7.6).

### 7.2 Data loading

```
                 start
                   │
     ┌─────────────┴──────────────┐
     ▼                            ▼
 small tables                 large tables
 (headline, demographics,     (voter rolls, maps,
  polling-centre files)        rare polling-centre files)
     │                            │
 load into memory             DuckDB view over the
 as DuckDB tables             lake URL; streams with
     │                        HTTP range requests
     └─────────────┬──────────────┘
                   ▼
           one DuckDB database,
           one name per dataset
```

- At start, load the two headline tables and the three demographics tables into memory
  (about 2 MB of Parquet). Lookup tools need them fast.
- Every other dataset is a view over its lake URL. Polling-centre files are small, so the
  builder can also load them into memory the first time they are used.
- Voter rolls always stream. Never load one into memory.
- Freshness: every 10 minutes, check the `ETag` or `Last-Modified` of each in-memory file
  and reload the ones that changed. Builder's call: the exact interval and method.

### 7.3 Dataset catalogue

- A script, `scripts/build_catalogue.py`, reads every Data Catalogue page and writes
  `src/electiondata_mcp/catalogue.json`. One entry per dataset: name, title, description,
  Parquet URL, rows, size, columns with descriptions, and the election, state and level it
  covers where the page states them.
- The JSON file is committed. The server reads it at start and does not scrape at run time.
- Table names: keep the 30 Query Builder names exactly as in `datasets.ts`, so SQL from the
  site's Query Builder works unchanged. Give the other datasets names in the same pattern,
  for example `saluran_ballots_sgr_se15`, `voter_roll_kdh_se15`, `map_peninsular_2018_parlimen`.
- Rerun the script when the site adds datasets. A test checks that every URL in the
  JSON answers `200` to a `HEAD` request.

### 7.4 Tools

All names and fields are proposals; the builder can rename for clarity.

**Find and look up (lake)**

| Tool | Input | Output |
|---|---|---|
| `search` | free text, optional kind (seat, candidate, party), optional state | Ranked matches with the exact keys other tools need: seat name + state, `candidate_uid`, `party_uid`. Uses DuckDB `jaro_winkler_similarity` on lower-case text and `ILIKE`, no extra extension. (The function is case-sensitive: 'Lembah Pantai' vs 'lembah pantai' scores 0.90, not 1.0.) |
| `seat_history` | seat, state, optional `lineage` flag | Every contest for the seat, newest first: date, election, winner, party, coalition, majority, turnout. With `lineage=true`, adds ancestor seats and boundary changes from the API. |
| `contest_result` | election, state, seat | Full ballot (all candidates, votes, share, result) and contest stats. |
| `candidate_profile` | `candidate_uid` | Every contest the candidate stood in, with party at the time and result. |
| `election_summary` | election, state or `Malaysia` | Seats won and votes by party and coalition, turnout, number of contests, uncontested seats. |
| `byelections` | optional state, optional date range | All by-elections with winner, majority, turnout. |
| `seat_demographics` | seat, state, optional election | Voters by sex, age band, ethnicity and voter type. |
| `polling_centre_results` | election, state, seat, optional level (`dm` or `saluran`) | Results per polling district or stream, for elections that have the data. Says clearly when an election has no such data. |
| `party_history` | party name or UID, state, type (`parlimen` or `dun`) | Seats and votes over time. Uses the API `maps_to` so old names (PERIKATAN) join with current ones (BN). |
| `find_seat_by_location` | latitude, longitude, optional year | Parliament seat, DUN seat and polling district that contain the point, for the boundary set in force at that year. |

**Live (API)**

| Tool | Input | Output |
|---|---|---|
| `live_turnout` | election id (e.g. `mlk-se16`), optional level and seat | Latest turnout snapshot with its MYT time, or one 5-minute slot. Says when the election has no live feed. |

**Analysis (lake, SQL)**

| Tool | Input | Output |
|---|---|---|
| `list_datasets` | optional filter text | Dataset names, titles, coverage, row counts, and whether it streams. |
| `describe_dataset` | name | Columns with types and descriptions, coverage notes. |
| `run_sql` | one SQL query, optional row limit | Columns, rows, row count, `truncated` flag, time taken. |

**Resource:** `electiondata://guide`. A short guide: what each dataset holds, how seats and
elections are named (`P.052 Bayan Baru`, `GE-15`, `SE-15`, `BY-ELECTION`), known traps
(SE-16 exists in several states, uncontested seats have zero votes and null percentages,
seat names change across boundary years), and two or three worked SQL examples.

### 7.5 SQL safety

Measured on 2026-11-15 with DuckDB 2.0.0: these settings let lake reads pass and block other
web hosts, local files, `COPY ... TO`, `ATTACH`, `INSTALL`, and setting changes.

```sql
LOAD httpfs;
SET allow_persistent_secrets = false;
SET allowed_directories = ['https://lake.electiondata.my/'];
SET allowed_paths = ['<CA certificate bundle path>'];  -- needed for HTTPS
SET enable_external_access = false;
SET lock_configuration = true;
```

Apply them after the in-memory tables and views are created. On top of that:

1. Accept one statement only, and only `SELECT` or `WITH ... SELECT`. Check with DuckDB's
   own parser (`json_serialize_sql`), not a regular expression.
2. Any query on a voter roll must have `LIMIT` of 10,000 or less, or aggregate.
3. Default 200 rows returned, maximum 2,000. Always return `truncated` and the full row count
   when cheap to get.
4. Time limit per query: 30 seconds, then interrupt the connection.
5. Memory limit for DuckDB: set from an environment variable, default 1 GB.
6. Each request gets its own cursor from one shared connection, so in-memory tables are shared.

### 7.6 Point-in-seat lookup

Measured on 2026-11-15:

| Option | Size added | Result for 3.1390, 101.6869 |
|---|---|---|
| DuckDB `spatial` extension, `ST_Contains` | 80 MB | `P.121 Lembah Pantai` |
| `shapely` (pulls in `numpy`) | 62 MB | not run |
| **DuckDB core + short Python test** | **0 MB** | **`P.121 Lembah Pantai`** |

**Call: DuckDB core + short Python test.**

1. DuckDB 2.0 core has `st_intersects_extent` (bounding-box test) and `ST_AsGeoJSON`.
   The box test cut the 166 seats of the 2018 Peninsular Parliament map to 3 candidates.
2. An even-odd ray test in plain Python (about 15 lines, holes and multi-polygons handled)
   picks the one seat that contains the point.
3. Needs one unit test with known points, including a point inside a hole and a point
   on the coast outside every seat.

Choose the map set by year: the newest delimitation on or before the given year, per region
(Peninsular, Sabah, Sarawak). Cache parsed shapes in memory after first use.

### 7.7 API client

- Key from environment variable `ELECTIONDATA_API_KEY`. Never logged, never returned.
- Without a key, the server still starts; API tools return a clear "not configured" message.
- Cache API responses in memory: 300 s for most, 30 s for live turnout, matching the API.
- Collapse identical requests in flight into one, so 1,000 users asking for turnout at the
  same moment cause one API call.
- ID mapping: the lake names a seat by `seat` + `state` (`P.109 Kapar`, `Selangor`). The API
  `/seats/results` needs a slug (`p001-padang-besar-perlis`). Get the slugs from
  `/seats/dropdown` (cached), and match on seat name + state. Do not build slugs by hand.
- Behind a proxy, the rate limit reads the client IP from the proxy header the host sets.

### 7.8 Transport, deployment, limits

- Streamable HTTP at `/mcp`, stateless, JSON responses. `GET /health` for the host.
- stdio when started with `--stdio`, for local Claude Code use.
- Docker image: minimal build measured at 75 MB download, 239 MB unpacked
  (`distroless/cc` base, uv's standalone Python, stripped libraries, `httpfs` included in
  the image so start needs no download).
- Per-client-IP request limit, default 60 tool calls per minute, set by environment variable.
- Logs: one line per tool call with tool name, time taken, row count and error type.
  No query text from users in logs by default.

### 7.9 Tests

- Unit tests per tool against a small local copy of a few lake files (fixture Parquet in
  `tests/data/`), so tests run offline.
- One set of live tests, marked and skipped by default, that hit the real lake and API.
- Safety tests: each blocked case in 7.5 must raise.
- Known answers as checks against independent sources, for example GE-15 seat totals by
  coalition and the GE-15 result of `P.121 Lembah Pantai`.

## 8. Decided by the owner

- Approach C: lake first, API for live turnout, lineage and party name mapping.
- Python.
- Lightweight is a requirement.
- Public GitHub repo `electiondata_mcp`.

## 9. Calls made by the designer (open to change)

- Tool list and names in 7.4.
- Point-in-seat with DuckDB core and a short Python test, not `spatial` or `shapely` (7.6).
- Freshness check every 10 minutes (7.2).
- Row limits 200 / 2,000, query time limit 30 s, rate limit 60 calls per minute.
- No login for MCP clients by default (see 10).

## 10. Open questions for the owner

1. **Who can call the server?** Open to anyone, or only callers with a shared token
   (for example, only the friend's app and your own connectors)?
2. **License for the code.** The data is CC0. The code needs its own license.
3. **Host.** The Docker image runs anywhere. The host can be chosen after the build.

## 11. Left to the builder

- Exact tool argument names, response shapes, and descriptions for the model.
- Fixture data choice for offline tests.
- How the catalogue script reads each page (the pages embed JSON in an HTML attribute).
- The exact CA certificate path in the image.

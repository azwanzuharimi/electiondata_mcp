# electiondata_mcp

An MCP server for Malaysian election data from [ElectionData.MY](https://electiondata.my/).

It lets an AI chat answer questions about every federal and state election since 1955:
seat histories, full contest results, candidates, parties, by-elections, polling-centre
results, voter demographics, boundary maps, and live turnout on polling day. It can also
run its own read-only SQL over all 208 datasets in the ElectionData.MY data lake.

Every answer lists its sources (dataset, file link, data-as-of date) and the matching
electiondata.my dashboard page, so a person can check a number before using it.

## Supported platforms

| OS | ARM | x86 |
|---|---|---|
| macOS | yes | yes |
| Windows | yes | yes |
| Linux | yes | yes |

Python 3.12 to 3.14. Every dependency installs from a ready-made build, so no compiler is
needed. CI runs the tests and the installers on all six platforms.

## Requirements

- An internet connection. The server reads the public data lake at `lake.electiondata.my`.
- Nothing else for the one-command install: it installs [uv](https://docs.astral.sh/uv/),
  which brings its own Python.
- Optional: a free ElectionData.MY API key for three tools (`live_turnout`, `seat_lineage`,
  `party_history`). Get one at https://electiondata.my/console/. All other tools work without it.

## Install

One command per platform. Each one installs uv if it is missing, installs the
`electiondata-mcp` command, runs a self-check, and prints how to connect it.

macOS and Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/azwanzuharimi/electiondata_mcp/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/azwanzuharimi/electiondata_mcp/main/install.ps1 | iex"
```

Docker (any OS, `linux/amd64` and `linux/arm64`):

```sh
docker run -p 8000:8000 ghcr.io/azwanzuharimi/electiondata_mcp
```

Check an install at any time:

```sh
electiondata-mcp --check
```

## Connect

**Claude Code:**

```sh
claude mcp add electiondata -- electiondata-mcp
```

With an API key:

```sh
claude mcp add electiondata -e ELECTIONDATA_API_KEY=your-key -- electiondata-mcp
```

**Claude Desktop:** add this to `claude_desktop_config.json` and restart Claude Desktop. Use
the full path that the installer printed if `electiondata-mcp` is not found.

```json
{
  "mcpServers": {
    "electiondata": {
      "command": "electiondata-mcp",
      "env": { "ELECTIONDATA_API_KEY": "your-key" }
    }
  }
}
```

**Web chat apps and claude.ai:** run the server over HTTP, put it behind HTTPS on a public
address, and add `https://your-host/mcp` as a custom connector.

```sh
electiondata-mcp --http --host 0.0.0.0 --port 8000
```

Any MCP client that speaks Streamable HTTP can use `http://your-host:8000/mcp`. `GET /health`
returns `{"ok": true}`.

## Settings

| Environment variable | Default | Meaning |
|---|---|---|
| `ELECTIONDATA_API_KEY` | not set | Key for the API tools. Never logged or returned. |
| `RATE_LIMIT_PER_MIN` | `60` | HTTP requests per minute per client IP. `0` turns it off. |
| `DUCKDB_MEMORY_LIMIT` | `1GB` | Memory limit for queries. |
| `QUERY_TIMEOUT_S` | `30` | Time limit per query, in seconds. |

## Tools

| Group | Tools |
|---|---|
| Find | `search` (seats, candidates, parties from loose text) |
| Look up | `seat_history`, `contest_result`, `candidate_profile`, `election_summary`, `byelections`, `seat_demographics`, `polling_centre_results`, `find_seat_by_location` |
| Analyse | `swing`, `marginal_seats`, `compare`, `seat_features`, `run_sql` |
| Discover | `search_datasets`, `describe_dataset`, `join_guide`, `find_dashboard`, `data_access` |
| API (key needed) | `live_turnout`, `seat_lineage`, `party_history` |

Resource `electiondata://guide` explains names, definitions, joins, traps and worked SQL.

## Accuracy

- A seat or name that matches more than one thing returns the choices, not a guess. A match on
  part of a name says so in a `warning`.
- `run_sql` can read only the data lake. Other files, other websites, writes and setting changes
  are blocked inside DuckDB.
- A daily check runs on the live lake: one winner per contest, majority = winner minus runner-up,
  valid votes = sum of candidate votes, shares add up to 100%, turnout and majority formulas,
  and polling-centre totals = seat totals for every polling-centre file. GE-15 seat counts are
  checked against the published result.
- Live turnout is marked provisional and carries the SPR snapshot time in MYT.

## Development

```sh
git clone https://github.com/azwanzuharimi/electiondata_mcp
cd electiondata_mcp
uv sync
uv run pytest              # offline tests
uv run pytest -m live      # tests against the live lake (and the API if ELECTIONDATA_API_KEY is set)
uv run python scripts/build_catalogue.py   # refresh the dataset list and dashboard index
```

Design: [docs/superpowers/specs/2026-11-15-electiondata-mcp-design.md](docs/superpowers/specs/2026-11-15-electiondata-mcp-design.md).

## Data credit

All election data comes from [ElectionData.MY](https://electiondata.my/), the public face of
the Malaysian Election Corpus (MECo), curated by Thevesh Thevananthan. The data is dedicated to
the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
Please credit them when you use this server.

Cite the corpus as:

> Thevesh Thevananthan. *The Malaysian Election Corpus (MECo): Federal and State-Level Election
> Results from 1955 to 2025.* Scientific Data (2025).
> [doi:10.1038/s41597-025-06502-7](https://doi.org/10.1038/s41597-025-06502-7)

The official source of all Malaysian election results is the
[Election Commission of Malaysia (SPR)](https://spr.gov.my).

This project is not affiliated with ElectionData.MY or SPR.

## License

The code is under the [MIT License](LICENSE). The data keeps its own CC0 dedication.

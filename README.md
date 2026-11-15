# electiondata_mcp

An MCP server for Malaysian election data from [ElectionData.MY](https://electiondata.my/).
It lets an AI chat look up seats, candidates, parties and elections from 1955 to today,
run its own analysis with SQL, follow live turnout on polling day, and find the right
dataset or dashboard page.

**Status: in progress. The server is not ready to install yet.**
Design: [docs/superpowers/specs/2026-11-15-electiondata-mcp-design.md](docs/superpowers/specs/2026-11-15-electiondata-mcp-design.md).
Build plan: [docs/superpowers/plans/2026-11-15-electiondata-mcp.md](docs/superpowers/plans/2026-11-15-electiondata-mcp.md).

## Supported platforms

| OS | ARM | x86 |
|---|---|---|
| macOS | yes | yes |
| Windows | yes | yes |
| Linux | yes | yes |

Python 3.12 to 3.14. Every dependency installs from a ready-made build, so no compiler is
needed. CI runs the tests on all six platforms.

## Install (planned, not working yet)

One command per platform. Each one installs [uv](https://docs.astral.sh/uv/) if it is
missing, then installs the `electiondata-mcp` command.

macOS and Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/azwanzuharimi/electiondata_mcp/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/azwanzuharimi/electiondata_mcp/main/install.ps1 | iex"
```

Docker (any OS):

```sh
docker run -p 8000:8000 ghcr.io/azwanzuharimi/electiondata_mcp
```

Full requirements, connect steps for Claude Code, Claude Desktop and claude.ai, and all
settings come with the first release. Every command in this file will be tested before then.

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

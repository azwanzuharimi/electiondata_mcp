import argparse
import logging
import sys
from importlib.metadata import version


def build():
    from electiondata_mcp.api import Api
    from electiondata_mcp.catalogue import load_catalogue, load_pages
    from electiondata_mcp.config import Config
    from electiondata_mcp.lake import Lake
    from electiondata_mcp.server import build_server

    cfg = Config.from_env()
    cat = load_catalogue()
    lake = Lake(cat, memory_limit=cfg.memory_limit, timeout_s=cfg.query_timeout_s)
    return cfg, lake, build_server(lake, Api(cfg.api_key), cat, load_pages())


def check() -> int:
    cfg, lake, _ = build()
    n = lake.run_sql("SELECT count(*) FROM headline_stats")["rows"][0][0]
    print(
        f"electiondata-mcp {version('electiondata-mcp')}: OK. Lake reachable, {n} contests loaded. API key: {'set' if cfg.api_key else 'not set (API tools off)'}."
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        prog="electiondata-mcp",
        description="MCP server for Malaysian election data from ElectionData.MY.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--stdio",
        action="store_true",
        help="talk over stdin/stdout (default; for Claude Code and Claude Desktop)",
    )
    mode.add_argument(
        "--http", action="store_true", help="serve Streamable HTTP at /mcp"
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="load the lake, run one query, print OK, exit",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP host (default 127.0.0.1; use 0.0.0.0 to serve others)",
    )
    p.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    p.add_argument("--version", action="version", version=version("electiondata-mcp"))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    if args.check:
        sys.exit(check())
    cfg, _, server = build()
    if args.http:
        import uvicorn

        from electiondata_mcp.server import RateLimit

        app = server.streamable_http_app(
            stateless_http=True, json_response=True, host=args.host
        )
        uvicorn.run(
            RateLimit(app, cfg.rate_limit_per_min),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()

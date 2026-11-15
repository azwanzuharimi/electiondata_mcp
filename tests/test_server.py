import re
from pathlib import Path

import pytest
from mcp import Client
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from conftest import CATALOGUE, PAGES
from electiondata_mcp.api import Api
from electiondata_mcp.server import RateLimit, build_server

TOOLS = {
    "search", "seat_history", "contest_result", "candidate_profile", "election_summary", "byelections", "seat_demographics",
    "polling_centre_results", "swing", "marginal_seats", "compare", "seat_features", "find_seat_by_location", "search_datasets",
    "describe_dataset", "join_guide", "find_dashboard", "data_access", "run_sql", "live_turnout", "party_history", "seat_lineage",
}


@pytest.fixture(scope="module")
def server(lake):
    return build_server(lake, Api(None), CATALOGUE, PAGES)


async def test_lists_all_tools(server):
    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == TOOLS


async def test_contest_result_has_sources_and_links(server):
    async with Client(server) as c:
        r = await c.call_tool("contest_result", {"election": "GE-15", "seat": "lembah pantai"})
    assert not r.is_error
    data = r.structured_content
    assert data["contests"][0]["ballot"][0]["votes"] == 35359
    assert data["sources"][0]["parquet"].startswith("https://lake.electiondata.my/")
    assert "https://electiondata.my/seats/parlimen/p121-lembah-pantai-wp-kuala-lumpur/" in data["links"]


async def test_errors_are_plain_messages(server):
    async with Client(server) as c:
        r = await c.call_tool("candidate_profile", {"candidate_uid": "NOPE0"})
        assert r.is_error and "search" in r.content[0].text
        r = await c.call_tool("run_sql", {"sql": "DELETE FROM headline_stats"})
        assert r.is_error


async def test_api_tool_without_key(server):
    async with Client(server) as c:
        r = await c.call_tool("live_turnout", {"election": "mlk-se16"})
    assert "ELECTIONDATA_API_KEY" in r.structured_content["error"]


async def test_guide_resource_and_examples_run(server, lake):
    async with Client(server) as c:
        text = (await c.read_resource("electiondata://guide")).contents[0].text
    assert "doi:10.1038/s41597-025-06502-7" in text
    blocks = re.findall(r"```sql\n(.*?)```", text, re.S)
    assert len(blocks) == 3
    for sql in blocks:
        assert lake.run_sql(sql)["row_count"] > 0


def test_rate_limit():
    inner = Starlette(routes=[Route("/mcp", lambda r: PlainTextResponse("ok"), methods=["POST"])])
    client = TestClient(RateLimit(inner, per_minute=3))
    codes = [client.post("/mcp").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert client.post("/mcp", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 200

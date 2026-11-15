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
    assert r.is_error and "ELECTIONDATA_API_KEY" in r.content[0].text


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
    assert client.post("/mcp", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 429


def test_rate_limit_behind_proxy_uses_last_forwarded_value():
    inner = Starlette(routes=[Route("/mcp", lambda r: PlainTextResponse("ok"), methods=["POST"])])
    client = TestClient(RateLimit(inner, per_minute=2, trust_proxy=True))
    spoof = [client.post("/mcp", headers={"x-forwarded-for": f"9.9.9.{i}, 5.5.5.5"}).status_code for i in range(3)]
    assert spoof == [200, 200, 429]
    assert client.post("/mcp", headers={"x-forwarded-for": "6.6.6.6"}).status_code == 200


async def test_every_data_tool_names_sources(server):
    calls = {
        "find_seat_by_location": {"lat": 3.1390, "lon": 101.6869},
        "seat_history": {"seat": "lembah pantai"},
        "run_sql": {"sql": "SELECT count(*) FROM headline_stats"},
    }
    async with Client(server) as c:
        for name, args in calls.items():
            r = await c.call_tool(name, args)
            assert r.structured_content.get("sources"), name


async def call(server, tool, args):
    async with Client(server) as c:
        return await c.call_tool(tool, args)


@pytest.mark.parametrize(
    "tool,args,text",
    [
        ("contest_result", {"election": "GE-15", "seat": "Atlantis"}, "No seat matches"),
        ("election_summary", {"election": "GE-99"}, "GE-15"),
        ("election_summary", {"election": "SE-15", "state": "W.P. Kuala Lumpur"}, "GE-15"),
        ("marginal_seats", {"election": "XX"}, "GE-15"),
        ("marginal_seats", {"election": "GE-15", "limit": -5}, "limit"),
        ("swing", {"election_from": "GE-14", "election_to": "GE-99", "group": "PH"}, "GE-15"),
        ("seat_features", {"election": "GE-99"}, "GE-15"),
        ("byelections", {"date_from": "19/11/2022"}, "YYYY-MM-DD"),
        ("contest_result", {"election": "GE-15", "seat": "Lembah Pantai", "date": "19-11-2022"}, "YYYY-MM-DD"),
        ("run_sql", {"sql": "SELECT 1", "limit": 0}, "limit"),
        ("search", {"text": "Kapar", "limit": 0}, "limit"),
        ("data_access", {}, "datasets"),
    ],
)
async def test_bad_input_is_an_error_with_a_plain_message(server, tool, args, text):
    r = await call(server, tool, args)
    assert r.is_error, r.content[0].text
    msg = r.content[0].text
    assert text in msg and "Binder Error" not in msg and "Conversion Error" not in msg


async def test_swing_error_keeps_quotes(server):
    r = await call(server, "swing", {"election_from": "GE-14", "election_to": "GE-15", "group": "NOPE"})
    assert r.is_error and "'NOPE' did not stand" in r.content[0].text


async def test_run_sql_non_finite_values_are_null_in_text_and_structure(server):
    import json

    r = await call(server, "run_sql", {"sql": "SELECT 1/0 AS a, sqrt(-1) AS b"})
    assert not r.is_error
    text = json.loads(r.content[0].text, parse_constant=lambda c: pytest.fail(f"non-JSON value {c}"))
    assert text["rows"] == [[None, None]] == r.structured_content["rows"]


async def test_contest_result_scope_names_only_the_contested_seat(server):
    r = await call(server, "contest_result", {"election": "GE-15", "seat": "Lembah Pantai"})
    assert "P.121 Lembah Pantai" in r.structured_content["scope"]
    assert "P.100" not in r.structured_content["scope"] and "P.109" not in r.structured_content["scope"]


async def test_candidate_uid_is_case_insensitive(server):
    r = await call(server, "search", {"text": "Fahmi", "kind": "candidate"})
    uid = r.structured_content["candidates"][0]["candidate_uid"]
    r = await call(server, "candidate_profile", {"candidate_uid": uid.lower()})
    assert not r.is_error and r.structured_content["contests"]


async def test_marginal_seats_smallest_majority_in_votes_first(server):
    r = await call(server, "marginal_seats", {"election": "GE-15", "limit": 20})
    m = [s["majority"] for s in r.structured_content["seats"]]
    assert m == sorted(m)


async def test_known_data_issue_is_flagged(server):
    r = await call(server, "election_summary", {"election": "GE-15"})
    assert any("Pasir Puteh" in w for w in r.structured_content["data_warnings"])
    r = await call(server, "election_summary", {"election": "GE-15", "state": "Selangor"})
    assert "data_warnings" not in r.structured_content
    r = await call(server, "run_sql", {"sql": "SELECT sum(votes) FROM headline_ballots WHERE election = 'GE-15'"})
    assert any("Pasir Puteh" in w for w in r.structured_content["data_warnings"])


async def test_api_tools_name_sources_and_fail_as_errors(lake):
    from test_api import fake

    ok = build_server(lake, Api("k", transport=fake([])), CATALOGUE, PAGES)
    r = await call(ok, "live_turnout", {"election": "mlk-se16"})
    assert not r.is_error and r.structured_content["sources"][0]["provider"] == "ElectionData.MY API"
    bad = build_server(lake, Api("k", transport=fake([], status=401)), CATALOGUE, PAGES)
    r = await call(bad, "party_history", {"party": "PAS"})
    assert r.is_error and "401" in r.content[0].text


async def test_known_data_issue_reaches_indirect_queries_and_candidate_compare(server):
    for sql in ("SELECT sum(votes) FROM headline_ballots WHERE election LIKE 'GE-1%'", "SELECT count(*) FROM headline_stats WHERE election = 'GE-' || '15'"):
        r = await call(server, "run_sql", {"sql": sql})
        assert any("Pasir Puteh" in w for w in r.structured_content["data_warnings"]), sql
    r = await call(server, "run_sql", {"sql": "SELECT 1 AS x"})
    assert "data_warnings" not in r.structured_content

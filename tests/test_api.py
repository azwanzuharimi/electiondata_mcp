import asyncio

import httpx2
import pytest
from electiondata_mcp import api as a

PARTIES = {
    "data": [
        {
            "type": "coalition",
            "uid": "001-PERIKATAN",
            "maps_to": "001-BN",
            "acronym": "PERIKATAN",
            "name_en": "Alliance Party",
            "name_bm": "Parti Perikatan",
        },
        {
            "type": "coalition",
            "uid": "001-BN",
            "maps_to": "001-BN",
            "acronym": "BN",
            "name_en": "National Front",
            "name_bm": "Barisan Nasional",
        },
        {
            "type": "party",
            "uid": "046-AMANAH",
            "maps_to": "046-AMANAH",
            "acronym": "AMANAH",
            "name_en": "National Trust Party",
            "name_bm": "Parti Amanah Negara",
        },
    ]
}
SEATS = {
    "seats": [
        {
            "seat": "P.121 Lembah Pantai, W.P. Kuala Lumpur",
            "slug": "p121-lembah-pantai-wp-kuala-lumpur",
            "type": "parlimen",
        },
        {
            "seat": "P.121 Bau-Lundu, Sarawak",
            "slug": "p121-bau-lundu-sarawak",
            "type": "parlimen",
        },
    ]
}
VTO = {
    "timestamp": "2026-11-14 08:25:52",
    "slot": "0825",
    "state": {"overall_abs": 1, "overall_vto": 0.1},
    "parlimen": {"P.134 Masjid Tanah": {"overall_abs": 10}},
    "dun": {"N.25 Rim": {"overall_abs": 712, "overall_vto": 4.01}},
}


def fake(calls: list, status: int = 200):
    def handler(req: httpx2.Request) -> httpx2.Response:
        calls.append(
            (req.url.path, dict(req.url.params), req.headers.get("authorization"))
        )
        path = req.url.path.removeprefix("/v1")
        if status != 200:
            return httpx2.Response(status, json={"error": "Unauthorized"})
        body = {
            "/parties/dropdown": PARTIES,
            "/seats/dropdown": SEATS,
            "/vto/latest": VTO,
        }.get(path, {"results": [{"path": path}]})
        return httpx2.Response(200, json=body)

    return httpx2.MockTransport(handler)


async def test_no_key_message():
    r = await a.live_turnout(a.Api(None), "mlk-se16")
    assert "ELECTIONDATA_API_KEY" in r["error"]


async def test_concurrent_calls_share_one_request():
    calls = []
    api = a.Api("k", transport=fake(calls))
    await asyncio.gather(
        *(api.get("/vto/latest", {"election": "mlk-se16"}, ttl=30) for _ in range(5))
    )
    assert len(calls) == 1
    await api.get("/vto/latest", {"election": "mlk-se16"}, ttl=30)
    assert len(calls) == 1
    assert calls[0][2] == "Bearer k"


async def test_401_is_plain_error():
    r = await a.live_turnout(a.Api("bad", transport=fake([], status=401)), "mlk-se16")
    assert "key" in r["error"].lower()


async def test_party_history_uses_maps_to():
    calls = []
    r = await a.party_history(
        a.Api("k", transport=fake(calls)), "perikatan", state="Malaysia"
    )
    params = calls[-1][1]
    assert calls[-1][0] == "/v1/parties/results"
    assert params == {
        "type": "coalition",
        "uid": "001-BN",
        "state": "Malaysia",
        "election_type": "parlimen",
    }
    assert r["resolved"]["maps_to"] == "001-BN"


async def test_party_history_unknown():
    r = await a.party_history(a.Api("k", transport=fake([])), "nothing-like-this")
    assert "error" in r


async def test_seat_lineage_slug_from_dropdown():
    calls = []
    r = await a.seat_lineage(
        a.Api("k", transport=fake(calls)), "P.121 Lembah Pantai", "W.P. Kuala Lumpur"
    )
    assert calls[-1][1] == {
        "slug": "p121-lembah-pantai-wp-kuala-lumpur",
        "lineage": "true",
    }
    assert "voter_turnout" in r["scope"]


async def test_seat_lineage_ambiguous():
    r = await a.seat_lineage(a.Api("k", transport=fake([])), "P.121")
    assert "ambiguous" in r


async def test_live_turnout_filters_seat_and_labels_provisional():
    r = await a.live_turnout(a.Api("k", transport=fake([])), "mlk-se16", seat="rim")
    assert r["units"] == {"N.25 Rim": {"overall_abs": 712, "overall_vto": 4.01}}
    assert r["snapshot_time_myt"] == "2026-11-14 08:25:52"
    assert "provisional" in r["scope"].lower()


async def test_live_turnout_bad_time():
    with pytest.raises(ValueError):
        await a.live_turnout(
            a.Api("k", transport=fake([])), "mlk-se16", level="dun", time="0807"
        )


needs_key = pytest.mark.skipif(not __import__("os").environ.get("ELECTIONDATA_API_KEY"), reason="no ELECTIONDATA_API_KEY")


@pytest.mark.live
@needs_key
async def test_live_api_matches_lake():
    import os

    from electiondata_mcp.catalogue import load_catalogue
    from electiondata_mcp.lake import Lake
    from electiondata_mcp.lookups import seat_history

    api = a.Api(os.environ["ELECTIONDATA_API_KEY"])
    r = await a.seat_lineage(api, "P.121 Lembah Pantai", "W.P. Kuala Lumpur")
    ge15_api = next(x for x in r["results"] if x.get("election_name") == "GE-15")
    ge15_lake = next(x for x in seat_history(Lake(load_catalogue()), "P.121 Lembah Pantai")["history"] if x["election"] == "GE-15")
    assert (ge15_api["name"], ge15_api["majority"]) == (ge15_lake["winner"], ge15_lake["majority"])
    p = await a.party_history(api, "PERIKATAN")
    assert p["resolved"]["maps_to"] == "001-BN" and p["results"]

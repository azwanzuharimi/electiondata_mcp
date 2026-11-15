import asyncio
import re
import time

import httpx2

from electiondata_mcp.lookups import CREDIT

BASE = "https://api.electiondata.my/v1"
NOT_CONFIGURED = "API not configured: set ELECTIONDATA_API_KEY. Get a free key at https://electiondata.my/console/"
TTL = 300
TTL_LIVE = 30
STATUS_MESSAGES = {
    401: "The API rejected the key (401). Check ELECTIONDATA_API_KEY.",
    404: "Not found (404).",
    429: "The API is busy (429). Try again shortly.",
}


class ApiError(Exception):
    pass


class Api:
    def __init__(
        self,
        key: str | None,
        base: str = BASE,
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        self.key = key
        self.base = base
        self.transport = transport
        self._client: httpx2.AsyncClient | None = None
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}

    def client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(
                base_url=self.base,
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "User-Agent": "electiondata_mcp",
                },
                timeout=20,
                transport=self.transport,
            )
        return self._client

    async def get(self, path: str, params: dict, ttl: float = TTL) -> dict:
        if not self.key:
            raise ApiError(NOT_CONFIGURED)
        k = (path, tuple(sorted(params.items())))
        hit = self._cache.get(k)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        if k in self._inflight:
            return await asyncio.shield(self._inflight[k])
        fut = asyncio.get_running_loop().create_future()
        self._inflight[k] = fut
        try:
            r = await self.client().get(path, params=params)
            if r.status_code != 200:
                detail = ""
                try:
                    detail = r.json().get("error") or r.json().get("message") or ""
                except ValueError:
                    pass
                raise ApiError(
                    STATUS_MESSAGES.get(r.status_code, f"API error {r.status_code}.")
                    + (f" {detail}" if detail and r.status_code == 400 else "")
                )
            data = r.json()
            self._cache[k] = (time.monotonic() + ttl, data)
            fut.set_result(data)
            return data
        except httpx2.RequestError as e:
            err = ApiError(f"Could not reach the API: {type(e).__name__}.")
            fut.set_exception(err)
            fut.exception()
            raise err from e
        except ApiError as e:
            fut.set_exception(e)
            fut.exception()
            raise
        finally:
            self._inflight.pop(k, None)


async def live_turnout(
    api: Api,
    election: str,
    level: str | None = None,
    time: str | None = None,
    seat: str | None = None,
) -> dict:
    if level is not None and level not in ("parlimen", "dun", "dm"):
        raise ValueError("level must be 'parlimen', 'dun' or 'dm'")
    if time is not None and not (
        re.fullmatch(r"\d{4}", time)
        and int(time[2:]) % 5 == 0
        and "0800" <= time <= "1800"
        and int(time[2:]) < 60
    ):
        raise ValueError(
            "time must be a 5-minute slot as HHMM in MYT between 0800 and 1800, e.g. 0805 or 1430"
        )
    try:
        if time:
            data = await api.get(
                "/vto",
                {"election": election, "level": level or "dun", "time": time},
                ttl=TTL_LIVE,
            )
        else:
            data = await api.get("/vto/latest", {"election": election}, ttl=TTL_LIVE)
    except ApiError as e:
        return {"error": str(e)}
    units: dict = {}
    for lvl in ("parlimen", "dun", "dm"):
        if isinstance(data.get(lvl), dict) and (level is None or level == lvl):
            units.update(data[lvl])
    if seat:
        s = seat.lower()
        units = {k: v for k, v in units.items() if s in k.lower()}
    return {
        "scope": f"PROVISIONAL live turnout for {election} from the SPR feed via ElectionData.MY. Snapshots every 5 minutes, 08:00-18:00 MYT. "
        "*_abs = votes cast, *_vto = turnout %, *_electorate = voters the % is based on. Not a final result. "
        + CREDIT,
        "snapshot_time_myt": data.get("timestamp"),
        "slot": data.get("slot") or time,
        "state": data.get("state"),
        "units": units,
    }


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


async def party_history(
    api: Api, party: str, state: str = "Malaysia", election_type: str = "parlimen"
) -> dict:
    try:
        dropdown = (await api.get("/parties/dropdown", {}))["data"]
        q = _norm(party)
        hits = [
            d
            for d in dropdown
            if q
            in (
                _norm(d["uid"]),
                _norm(d["acronym"]),
                _norm(d.get("name_en") or ""),
                _norm(d.get("name_bm") or ""),
                _norm(d["uid"].split("-", 1)[-1]),
            )
        ]
        targets = {(d["type"], d["maps_to"]) for d in hits}
        if not targets:
            return {
                "error": f"No party or coalition matches '{party}'. Use search with kind='party'."
            }
        if len(targets) > 1:
            return {
                "ambiguous": [{"type": t, "uid": u} for t, u in sorted(targets)],
                "hint": "Pass the uid.",
            }
        ((kind, uid),) = targets
        data = await api.get(
            "/parties/results",
            {"type": kind, "uid": uid, "state": state, "election_type": election_type},
        )
    except ApiError as e:
        return {"error": str(e)}
    resolved = next(d for d in hits if d["maps_to"] == uid)
    return {
        "scope": f"{kind} {uid} in {state} ({election_type}), newest first. Old names are merged into the current one (known_as shows the name used at the time). {CREDIT}",
        "resolved": {
            "input": party,
            "type": kind,
            "maps_to": uid,
            "matched": resolved["uid"],
        },
        "results": data.get("results", []),
    }


async def seat_lineage(api: Api, seat: str, state: str | None = None) -> dict:
    try:
        seats = (await api.get("/seats/dropdown", {}))["seats"]
        want = _norm(f"{seat}, {state}") if state else _norm(seat)
        exact = [s for s in seats if _norm(s["seat"]) == want]
        hits = exact or [
            s
            for s in seats
            if _norm(s["seat"]).startswith(want) or want in _norm(s["seat"])
        ]
        if not hits:
            return {
                "error": f"No seat matches '{seat}'"
                + (f" in {state}" if state else "")
                + ". Use search to find the exact seat name."
            }
        if len(hits) > 1:
            return {
                "ambiguous": [s["seat"] for s in hits[:20]],
                "hint": "Pass the full seat name with code, and the state.",
            }
        data = await api.get(
            "/seats/results", {"slug": hits[0]["slug"], "lineage": "true"}
        )
    except ApiError as e:
        return {"error": str(e)}
    return {
        "scope": f"Results and boundary changes for {hits[0]['seat']}, newest first. Entries with change_en are boundary changes; their dates are approximate. "
        "In these API results voter_turnout is the number of votes cast and voter_turnout_perc is the percentage (in lake tables voter_turnout is the percentage). "
        + CREDIT,
        "seat": hits[0]["seat"],
        "slug": hits[0]["slug"],
        "results": data.get("results", []),
    }

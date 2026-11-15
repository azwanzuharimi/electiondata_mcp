import json
import re

from electiondata_mcp.lake import Lake
from electiondata_mcp.lookups import CREDIT

MAP_RE = re.compile(
    r"^map_(peninsular|sabah|sarawak)_(\d{4})_(parlimen|dun|dm)(_p\d+)?$"
)
LEVEL_COLUMN = {"parlimen": "parlimen", "dun": "dun", "dm": "dm"}


def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def contains(geometry: dict, x: float, y: float) -> bool:
    if geometry["type"] == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        return False
    return any(
        _in_ring(x, y, p[0]) and not any(_in_ring(x, y, h) for h in p[1:])
        for p in polygons
    )


_SHAPES: dict[tuple[int, str], list] = {}


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    xs = [pt[0] for p in polys for ring in p for pt in ring]
    ys = [pt[1] for p in polys for ring in p for pt in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _shapes(lake: Lake, name: str, level: str) -> list:
    key = (id(lake), name)
    if key not in _SHAPES:
        lake.ensure([name])
        rows = lake.records(f"SELECT state, {LEVEL_COLUMN[level]} AS seat, ST_AsGeoJSON(geometry) AS g FROM {name}")
        shapes = []
        for r in rows:
            g = json.loads(r["g"])
            if g["type"] in ("Polygon", "MultiPolygon"):
                shapes.append((r["state"], r["seat"], _bbox(g), g))
        _SHAPES[key] = shapes
    return _SHAPES[key]


def pick_maps(
    catalogue: list[dict],
    year: int | None,
    include_proposed: bool = False,
    levels: tuple[str, ...] = ("parlimen", "dun"),
) -> list[dict]:
    maps = []
    for e in catalogue:
        m = MAP_RE.match(e["name"])
        if m and m.group(3) in levels and (include_proposed or not m.group(4)):
            maps.append(
                {
                    "name": e["name"],
                    "region": m.group(1),
                    "year": int(m.group(2)),
                    "level": m.group(3),
                    "proposed": bool(m.group(4)),
                }
            )
    chosen = []
    for region in ("peninsular", "sabah", "sarawak"):
        for level in levels:
            for proposed in (False, True) if include_proposed else (False,):
                opts = [
                    m
                    for m in maps
                    if m["region"] == region
                    and m["level"] == level
                    and m["proposed"] == proposed
                    and (year is None or m["year"] <= year)
                ]
                if opts:
                    chosen.append(max(opts, key=lambda m: m["year"]))
    return chosen


def find_seat_by_location(
    lake: Lake,
    lat: float,
    lon: float,
    year: int | None = None,
    include_district: bool = False,
    include_proposed: bool = False,
    regions: list[str] | None = None,
) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat must be between -90 and 90, lon between -180 and 180")
    levels = ("parlimen", "dun", "dm") if include_district else ("parlimen", "dun")
    maps = [
        m
        for m in pick_maps(
            list(lake.catalogue.values()), year, include_proposed, levels
        )
        if not regions or m["region"] in regions
    ]
    point = f"POINT({lon} {lat})"
    matches = []
    for m in sorted(maps, key=lambda m: ("parlimen", "dun", "dm").index(m["level"])):
        if matches and m["region"] not in {x["region"] for x in matches}:
            continue
        if m["level"] == "dm":
            lake.ensure([m["name"]])
            rows = lake.records(
                f"SELECT state, dm AS seat, ST_AsGeoJSON(geometry) AS g FROM {m['name']} WHERE st_intersects_extent(geometry, ?::GEOMETRY)",
                [point],
            )
            candidates = [(r["state"], r["seat"], json.loads(r["g"])) for r in rows]
        else:
            candidates = [(s, n, g) for s, n, (x0, y0, x1, y1), g in _shapes(lake, m["name"], m["level"]) if x0 <= lon <= x1 and y0 <= lat <= y1]
        for state, seat, g in candidates:
            if contains(g, lon, lat):
                matches.append(
                    {
                        "level": m["level"],
                        "seat": seat,
                        "state": state,
                        "map": m["name"],
                        "map_year": m["year"],
                        "proposed": m["proposed"],
                        "region": m["region"],
                    }
                )
                break
    note = (
        ""
        if matches
        else "No seat found at this point. It may be at sea or outside Malaysia."
    )
    return {
        "scope": f"Seats containing ({lat}, {lon}) on the boundary maps in force "
        + (f"in {year}" if year else "now")
        + (", plus not-yet-gazetted proposals" if include_proposed else "")
        + f". {CREDIT}",
        "matches": matches,
        "note": note,
    }

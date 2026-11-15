import difflib
import json
import re
from importlib import resources

SITE = "https://electiondata.my/"
STOPWORDS = {
    "the",
    "of",
    "in",
    "for",
    "and",
    "a",
    "an",
    "data",
    "dataset",
    "datasets",
    "results",
    "result",
    "centre",
    "center",
    "station",
    "level",
    "by",
    "on",
    "with",
    "table",
}
SYNONYMS = {
    "polling": ["saluran", "polling"],
    "saluran": ["saluran"],
    "stream": ["saluran"],
    "district": ["dm", "district"],
    "constituency": ["seat", "constituency", "parlimen", "dun"],
    "boundary": ["delimitation", "boundaries", "boundary"],
    "boundaries": ["delimitation", "boundaries"],
    "map": ["delimitation", "map"],
    "maps": ["delimitation", "map"],
    "redelineation": ["delimitation"],
    "parliament": ["parlimen", "parliament", "federal", "pru"],
    "parliamentary": ["parlimen", "parliament", "federal", "pru"],
    "federal": ["parlimen", "federal", "pru"],
    "general": ["federal", "pru", "ge", "general"],
    "state": ["state", "dun", "prn"],
    "assembly": ["dun", "prn", "assembly"],
    "voters": ["voter", "voters", "electorate"],
    "demographic": ["demographic", "ethnic", "age", "sex"],
    "demographics": ["demographic", "ethnic", "age", "sex"],
    "turnout": ["turnout"],
    "ethnicity": ["ethnic"],
    "race": ["ethnic"],
    "gender": ["sex"],
    "candidates": ["candidate"],
    "parties": ["party"],
    "seats": ["seat"],
    "byelection": ["by-election", "byelection", "pru kecil"],
    "byelections": ["by-election", "byelection"],
}
STATE_CODES = {
    "johor": "jhr",
    "kedah": "kdh",
    "kelantan": "ktn",
    "melaka": "mlk",
    "negeri": "nsn",
    "sembilan": "nsn",
    "pahang": "phg",
    "perlis": "pls",
    "pinang": "png",
    "penang": "png",
    "perak": "prk",
    "sabah": "sbh",
    "selangor": "sgr",
    "sarawak": "swk",
    "terengganu": "trg",
}
JOIN_KEYS = {
    "headline_ballots": [
        "election",
        "state",
        "seat",
        "date",
        "candidate_uid",
        "party_uid",
        "coalition_uid",
    ],
    "headline_stats": ["election", "state", "seat", "date"],
    "voter_demographics": ["election", "state", "seat", "date"],
    "saluran_ballots": [
        "election",
        "state",
        "seat",
        "dm",
        "pm",
        "saluran",
        "candidate_uid",
        "party_uid",
        "coalition_uid",
    ],
    "saluran_stats": ["election", "state", "seat", "dm", "pm", "saluran"],
    "voter_roll": ["state", "parlimen", "dun", "dm", "pm", "saluran"],
    "map": ["state", "parlimen", "dun", "dm"],
}
TRAPS = {
    "demographics_ge": "voter_demographics rows labelled GE-15 cover all 822 seats of the GE-15 voter roll: 222 Parliament (P.) and 600 state (N.) seats. "
    "State seat results are labelled SE-xx, so a join on election drops the 600 state seats. Join state seats on state + seat and pick the roll date yourself.",
    "multi_state": "State elections share names across states (SE-16 exists in several states). Always filter or join on state too.",
    "boundaries": "Seat names and numbers change across boundary years. A join across elections on seat alone compares different areas. Use seat_lineage, or flag seats whose name changed.",
    "uncontested": "Uncontested seats have zero votes and null percentages. Exclude them from averages and swings, or handle the nulls.",
    "byelection": "By-elections reuse the seat name of the general election. Add date to the join key for by-elections.",
}
PAIR_RULES = [
    (
        {"headline_ballots", "headline_stats"},
        ["election", "state", "seat", "date"],
        "One stats row per contest, many ballot rows per contest.",
        ["byelection", "uncontested"],
    ),
    (
        {"headline_ballots", "voter_demographics"},
        ["state", "seat", "election"],
        "Parliament seats only for GE-xx labels.",
        ["demographics_ge"],
    ),
    (
        {"headline_stats", "voter_demographics"},
        ["state", "seat", "election"],
        "Parliament seats only for GE-xx labels.",
        ["demographics_ge"],
    ),
    (
        {"saluran_ballots", "saluran_stats"},
        ["election", "state", "seat", "dm", "pm", "saluran"],
        "One stats row per polling stream, many ballot rows per stream.",
        [],
    ),
    (
        {"saluran_ballots", "headline_ballots"},
        ["election", "state", "seat", "candidate_uid"],
        "Sum polling streams to get the seat total.",
        ["multi_state"],
    ),
    (
        {"saluran_stats", "headline_stats"},
        ["election", "state", "seat"],
        "Sum polling streams to get the seat total.",
        ["multi_state"],
    ),
    (
        {"saluran_ballots", "headline_stats"},
        ["election", "state", "seat"],
        "",
        ["multi_state"],
    ),
    (
        {"saluran_stats", "headline_ballots"},
        ["election", "state", "seat"],
        "",
        ["multi_state"],
    ),
    (
        {"voter_roll", "headline_stats"},
        ["state", "parlimen = seat (P.) or dun = seat (N.)"],
        "Pick the election that the roll was used for.",
        ["boundaries"],
    ),
    (
        {"voter_roll", "headline_ballots"},
        ["state", "parlimen = seat (P.) or dun = seat (N.)"],
        "Pick the election that the roll was used for.",
        ["boundaries"],
    ),
    (
        {"voter_roll", "saluran_stats"},
        ["state", "dm", "pm", "saluran"],
        "Voters per stream from the roll should match voters_total.",
        [],
    ),
    ({"voter_roll", "saluran_ballots"}, ["state", "dm", "pm", "saluran"], "", []),
    (
        {"map", "headline_stats"},
        ["state", "parlimen = seat (P.) or dun = seat (N.)"],
        "Use the map in force at the election date.",
        ["boundaries"],
    ),
    (
        {"map", "headline_ballots"},
        ["state", "parlimen = seat (P.) or dun = seat (N.)"],
        "Use the map in force at the election date.",
        ["boundaries"],
    ),
    (
        {"map", "voter_demographics"},
        ["state", "parlimen = seat (P.) or dun = seat (N.)"],
        "",
        ["boundaries", "demographics_ge"],
    ),
]


def load_catalogue() -> list[dict]:
    return json.loads(
        resources.files("electiondata_mcp")
        .joinpath("catalogue.json")
        .read_text(encoding="utf-8")
    )


def load_pages() -> dict[str, list[str]]:
    return json.loads(
        resources.files("electiondata_mcp")
        .joinpath("pages.json")
        .read_text(encoding="utf-8")
    )


def family(name: str) -> str:
    for f in (
        "headline_ballots",
        "headline_stats",
        "voter_demographics",
        "saluran_ballots",
        "saluran_stats",
        "voter_roll",
        "map",
    ):
        if name.startswith(f):
            return f
    return name


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9.\-]+", text.lower()) if w not in STOPWORDS]


def _alternatives(word: str) -> list[str]:
    alts = SYNONYMS.get(word, [word])
    if word in STATE_CODES:
        alts = alts + [STATE_CODES[word]] + ([] if word in ("sabah", "sarawak") else ["peninsular"])
    return alts


def search_datasets(
    cat: list[dict], query: str, group: str | None = None, limit: int = 20
) -> list[dict]:
    words = _words(query)
    if not words:
        return []
    hits = []
    for e in cat:
        if group and e["group"] != group:
            continue
        fields = {
            "title": (e["title"] or "").lower(),
            "name": e["name"].lower().replace("_", " ") + " " + (e.get("slug") or ""),
            "columns": " ".join(
                f"{col['name']} {col.get('description') or ''}" for col in e["columns"]
            ).lower(),
            "description": " ".join(
                str(e.get(k) or "")
                for k in ("description", "category", "subcategory", "data_as_of")
            ).lower(),
        }
        score, matched = 0, set()
        for w in words:
            found = [
                f
                for f, text in fields.items()
                if any(a in text for a in _alternatives(w))
            ]
            if not found:
                break
            matched.update(found)
            score += sum(
                {"title": 3, "name": 3, "columns": 2, "description": 1}[f]
                for f in found
            )
        else:
            hits.append((score, e, sorted(matched)))
    hits.sort(key=lambda h: (-h[0], h[1]["name"]))
    return [
        {
            "name": e["name"],
            "title": e["title"],
            "group": e["group"],
            "rows": e["rows"],
            "streams": e["streams"],
            "matched_on": m,
        }
        for _, e, m in hits[:limit]
    ]


def _get(cat: list[dict], name: str) -> dict:
    for e in cat:
        if e["name"] == name:
            return e
    close = difflib.get_close_matches(name, [e["name"] for e in cat], n=5, cutoff=0.5)
    raise KeyError(
        f"No dataset named '{name}'. Close names: {', '.join(close) or 'none'}. Use search_datasets to find one."
    )


def describe_dataset(cat: list[dict], name: str) -> dict:
    e = _get(cat, name)
    return {
        "name": e["name"],
        "title": e["title"],
        "description": e["description"],
        "data_as_of": e["data_as_of"],
        "rows": e["rows"],
        "streams": e["streams"],
        "columns": e["columns"],
        "join_keys": JOIN_KEYS.get(family(e["name"]), []),
        "links": {
            "catalogue_page": f"{SITE}data-catalogue/{e['slug']}"
            if e.get("slug")
            else None,
            "parquet": e["parquet"],
            "csv": e.get("csv"),
            **(e.get("other_links") or {}),
        },
        "note": "Streams from the lake on each query; filter or aggregate."
        if e["streams"]
        else "Small enough to query freely.",
    }


def join_guide(cat: list[dict], names: list[str]) -> dict:
    for n in names:
        _get(cat, n)
    pairs, trap_keys = [], set()
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            fa, fb = family(a), family(b)
            rule = next((r for r in PAIR_RULES if r[0] == {fa, fb}), None)
            if fa == fb:
                on, note, traps = (
                    JOIN_KEYS.get(fa, []),
                    "Same kind of table: stack with UNION ALL BY NAME, or join on the keys.",
                    ["multi_state"],
                )
            elif rule:
                _, on, note, traps = rule
            else:
                on, note, traps = (
                    sorted(set(JOIN_KEYS.get(fa, [])) & set(JOIN_KEYS.get(fb, []))),
                    "No tested rule for this pair; check the keys.",
                    [],
                )
            pairs.append({"a": a, "b": b, "on": on, "note": note})
            trap_keys.update(traps)
    if any(family(n) in ("headline_ballots", "headline_stats") for n in names):
        trap_keys.update({"multi_state", "uncontested"})
    return {"pairs": pairs, "traps": [TRAPS[k] for k in sorted(trap_keys)]}


def find_dashboard(
    pages: dict[str, list[str]], kind: str, text: str, limit: int = 10
) -> list[dict]:
    if kind not in pages:
        raise ValueError(f"kind must be one of: {', '.join(sorted(pages))}")
    needle = [w for w in re.split(r"[\s\-_/]+", text.lower()) if w]
    hits = [p for p in pages[kind] if all(w in p.lower() for w in needle)]
    hits.sort(key=len)
    return [{"url": SITE + p, "url_ms": SITE + "ms-MY/" + p} for p in hits[:limit]]


def data_access(
    cat: list[dict], names: list[str] | None = None, sql: str | None = None
) -> dict:
    if sql:
        names = sorted(
            {e["name"] for e in cat if re.search(rf"\b{re.escape(e['name'])}\b", sql)}
        )
    names = names or []
    if not names:
        raise ValueError("Name at least one dataset in datasets or in sql. Find names with search_datasets.")
    entries = [_get(cat, n) for n in names]
    views = "\n".join(
        f"con.execute(\"CREATE VIEW {e['name']} AS SELECT * FROM read_parquet('{e['parquet']}')\")"
        for e in entries
    )
    query = sql or (f"SELECT * FROM {names[0]} LIMIT 10" if names else "SELECT 1")
    first = entries[0]["parquet"] if entries else ""
    return {
        "datasets": [
            {
                "name": e["name"],
                "parquet": e["parquet"],
                "csv": e.get("csv"),
                "rows": e["rows"],
            }
            for e in entries
        ],
        "code": {
            "python_duckdb": f'import duckdb\ncon = duckdb.connect()\ncon.execute(\'INSTALL httpfs; LOAD httpfs\')\n{views}\ndf = con.execute("""{query}""").df()',
            "python_pandas": f"import pandas as pd\ndf = pd.read_parquet('{first}')",
            "r": f"library(duckdb)\ncon <- dbConnect(duckdb())\ndbExecute(con, 'INSTALL httpfs; LOAD httpfs')\ndf <- dbGetQuery(con, \"SELECT * FROM read_parquet('{first}')\")",
        },
        "note": "Runs on your own machine with the full data and no row limit. Data: ElectionData.MY, CC0.",
    }

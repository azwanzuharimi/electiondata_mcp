import html
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

SITE = "https://electiondata.my"
LAKE = "https://lake.electiondata.my/"
DATASETS_TS = "https://raw.githubusercontent.com/electiondata-my/meco-front/main/src/components/tools/query-builder/datasets.ts"
OUT = Path(__file__).resolve().parent.parent / "src" / "electiondata_mcp"
PAGE_KINDS = {
    "seats": "seat",
    "candidates": "candidate",
    "parties": "party",
    "coalitions": "coalition",
    "elections": "election",
    "redelineation": "redelineation",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "electiondata_mcp catalogue builder"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def unwrap(v):
    if isinstance(v, list) and len(v) == 2 and v[0] in (0, 1):
        return unwrap(v[1]) if v[0] == 0 else [unwrap(x) for x in v[1]]
    if isinstance(v, dict):
        return {k: unwrap(x) for k, x in v.items()}
    return v


def page_props(slug: str) -> dict:
    text = fetch(f"{SITE}/data-catalogue/{slug}")
    for raw in re.findall(r'props="([^"]+)"', text):
        s = html.unescape(raw)
        if LAKE in s and '"data"' in s:
            return unwrap(json.loads(s))
    raise ValueError(f"no dataset props on page {slug}")


def query_builder() -> tuple[dict[str, str], dict[str, str]]:
    ts = fetch(DATASETS_TS)
    block = ts.split("export const DATASETS", 1)[1].split("} as const", 1)[0]
    names = {
        url: name for name, url in re.findall(r'(\w+):\s*"(https://lake[^"]+)"', block)
    }
    desc_block = ts.split("export const DATASET_DESCRIPTIONS", 1)[1].split("};", 1)[0]
    descriptions = {k: v for k, v in re.findall(r'(\w+):\s*"([^"]+)"', desc_block)}
    return names, descriptions


def derive_name(parquet: str) -> str:
    path = parquet.removeprefix(LAKE).removesuffix(".parquet")
    folder, file = path.rsplit("/", 1)
    if folder == "results_saluran":
        scope, kind = file.rsplit("_", 1)
        return f"saluran_{kind}_{scope}"
    if folder == "voter_rolls":
        return "voter_roll_" + file.rsplit("_", 1)[0]
    if folder.startswith("maps/"):
        return "map_" + file
    return file


def entry(slug: str, qb: dict[str, str]) -> dict:
    p = page_props(slug)
    d = p["data"]
    dl = d.get("download") or {}
    parquet = (dl.get("parquet") or {}).get("link")
    if not parquet:
        parquet = next(
            (
                v["link"]
                for v in dl.values()
                if isinstance(v, dict) and str(v.get("link", "")).endswith(".parquet")
            ),
            None,
        )
    name = qb.get(parquet) or derive_name(parquet)
    return {
        "name": name,
        "slug": slug,
        "title": d.get("title"),
        "description": d.get("description"),
        "group": slug.split("-")[0]
        if not slug.startswith(("headline", "saluran", "voter"))
        else "-".join(slug.split("-")[:2]),
        "category": p.get("category"),
        "subcategory": p.get("subcategory"),
        "data_as_of": d.get("data_as_of"),
        "parquet": parquet,
        "csv": (dl.get("csv") or {}).get("link"),
        "other_links": {
            k: v.get("link")
            for k, v in dl.items()
            if isinstance(v, dict) and k not in ("parquet", "csv")
        },
        "rows": (dl.get("parquet") or {}).get("n_rows"),
        "size_bytes": (dl.get("parquet") or {}).get("size_bytes"),
        "columns": [
            {
                "name": f.get("name"),
                "title": f.get("title"),
                "description": f.get("description"),
            }
            for f in d.get("fields") or []
        ],
        "streams": name.startswith(("voter_roll_", "map_")),
        "query_builder": parquet in qb,
    }


def build_catalogue() -> list[dict]:
    index = fetch(f"{SITE}/data-catalogue")
    slugs = sorted(set(re.findall(r'"/data-catalogue/([a-z0-9-]+)"', index)))
    qb, qb_desc = query_builder()
    with ThreadPoolExecutor(8) as pool:
        entries = list(pool.map(lambda s: entry(s, qb), slugs))
    names = [e["name"] for e in entries]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate dataset names: {sorted(dupes)}")
    entries += [
        query_builder_only(url, name, qb_desc.get(name, ""))
        for url, name in qb.items()
        if name not in names
    ]
    return sorted(entries, key=lambda e: e["name"])


def query_builder_only(url: str, name: str, description: str) -> dict:
    con = duckdb.connect()
    con.execute("LOAD httpfs")
    cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{url}')").fetchall()
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{url}')").fetchone()[0]
    return {
        "name": name,
        "slug": None,
        "title": name.replace("_", " ").capitalize(),
        "description": description,
        "group": "seat-info",
        "category": None,
        "subcategory": None,
        "data_as_of": None,
        "parquet": url,
        "csv": None,
        "other_links": {},
        "rows": rows,
        "size_bytes": None,
        "columns": [
            {"name": c[0], "title": c[0], "description": f"[{c[1]}]"} for c in cols
        ],
        "streams": False,
        "query_builder": True,
    }


def build_pages() -> dict[str, list[str]]:
    xml = fetch(f"{SITE}/sitemap-0.xml")
    pages: dict[str, list[str]] = {k: [] for k in PAGE_KINDS.values()}
    for loc in re.findall(r"<loc>https://electiondata\.my/([^<]+)</loc>", xml):
        head = loc.split("/", 1)[0]
        if head in PAGE_KINDS and loc.rstrip("/") != head:
            pages[PAGE_KINDS[head]].append(loc)
    return pages


def main() -> None:
    cat = build_catalogue()
    (OUT / "catalogue.json").write_text(
        json.dumps(cat, indent=1, ensure_ascii=False) + "\n"
    )
    pages = build_pages()
    (OUT / "pages.json").write_text(json.dumps(pages, separators=(",", ":")) + "\n")
    print(
        f"{len(cat)} datasets, " + ", ".join(f"{k}={len(v)}" for k, v in pages.items()),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

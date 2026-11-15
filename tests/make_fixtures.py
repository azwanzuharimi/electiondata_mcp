import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
CAT = {
    e["name"]: e["parquet"]
    for e in json.loads(
        (ROOT.parent / "src" / "electiondata_mcp" / "catalogue.json").read_text()
    )
}
STATES = "state IN ('Selangor', 'W.P. Kuala Lumpur')"
EXTRACTS = {
    "headline_ballots": f"{STATES} OR election = 'BY-ELECTION'",
    "headline_stats": f"{STATES} OR election = 'BY-ELECTION'",
    "voter_demographics": STATES,
    "voter_demographics_sabah": "true",
    "voter_demographics_sarawak": "true",
    "saluran_ballots_ge15": "seat IN ('P.121 Lembah Pantai', 'P.109 Kapar')",
    "saluran_stats_ge15": "seat IN ('P.121 Lembah Pantai', 'P.109 Kapar')",
    "saluran_ballots_sgr_se15": "seat LIKE 'N.4%'",
    "saluran_stats_sgr_se15": "seat LIKE 'N.4%'",
    "voter_roll_ge15": "parlimen = 'P.121 Lembah Pantai' LIMIT 2000",
    "map_peninsular_2018_parlimen": STATES,
    "map_peninsular_2018_dun": "state = 'Selangor'",
    "map_peninsular_2003_parlimen": STATES,
}


def main() -> None:
    con = duckdb.connect()
    con.execute("LOAD httpfs")
    out = ROOT / "data"
    out.mkdir(exist_ok=True)
    for name, where in EXTRACTS.items():
        con.execute(
            f"COPY (SELECT * FROM read_parquet('{CAT[name]}') WHERE {where}) TO '{out / name}.parquet' (FORMAT parquet)"
        )


if __name__ == "__main__":
    main()

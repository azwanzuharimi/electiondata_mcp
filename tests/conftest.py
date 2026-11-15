import json
from pathlib import Path

import pytest
from electiondata_mcp.lake import Lake

DATA = Path(__file__).resolve().parent / "data"
PKG = Path(__file__).resolve().parent.parent / "src" / "electiondata_mcp"
CATALOGUE = json.loads((PKG / "catalogue.json").read_text())
PAGES = json.loads((PKG / "pages.json").read_text())
URL_TO_LOCAL = {
    e["parquet"]: str(DATA / f"{e['name']}.parquet")
    for e in CATALOGUE
    if (DATA / f"{e['name']}.parquet").exists()
}


def make_lake(**kw) -> Lake:
    return Lake(
        CATALOGUE,
        url_map=lambda u: URL_TO_LOCAL.get(u, u),
        extra_allowed_dirs=[str(DATA) + "/"],
        **kw,
    )


@pytest.fixture(scope="session")
def lake() -> Lake:
    return make_lake()


@pytest.fixture(scope="session")
def catalogue() -> list[dict]:
    return CATALOGUE


@pytest.fixture(scope="session")
def pages() -> dict:
    return PAGES

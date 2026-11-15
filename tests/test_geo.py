import pytest

from electiondata_mcp import geo

SQUARE_WITH_HOLE = {
    "type": "Polygon",
    "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
    ],
}
TWO_SQUARES = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]],
    ],
}


def test_contains_polygon_with_hole():
    assert geo.contains(SQUARE_WITH_HOLE, 2, 2)
    assert not geo.contains(SQUARE_WITH_HOLE, 5, 5)
    assert not geo.contains(SQUARE_WITH_HOLE, 11, 5)


def test_contains_multipolygon():
    assert geo.contains(TWO_SQUARES, 5.5, 5.5)
    assert not geo.contains(TWO_SQUARES, 3, 3)


def test_pick_maps_by_year(catalogue):
    names = {m["name"] for m in geo.pick_maps(catalogue, 2010)}
    assert (
        "map_peninsular_2003_parlimen" in names and "map_peninsular_2003_dun" in names
    )
    assert "map_sarawak_2005_parlimen" in names
    assert not any("2018" in n for n in names)


def test_pick_maps_skips_proposals_by_default(catalogue):
    names = {m["name"] for m in geo.pick_maps(catalogue, None)}
    assert "map_sarawak_2015_parlimen" in names and not any("_p1" in n for n in names)
    with_prop = {
        m["name"] for m in geo.pick_maps(catalogue, None, include_proposed=True)
    }
    assert "map_sarawak_2026_parlimen_p1" in with_prop


def test_find_seat_kl(lake):
    r = geo.find_seat_by_location(lake, 3.1390, 101.6869, regions=["peninsular"])
    seats = {(m["level"], m["seat"]) for m in r["matches"]}
    assert ("parlimen", "P.121 Lembah Pantai") in seats


def test_find_seat_older_map(lake):
    r = geo.find_seat_by_location(
        lake, 3.1390, 101.6869, year=2010, regions=["peninsular"]
    )
    assert r["matches"] and r["matches"][0]["map"] == "map_peninsular_2003_parlimen"


def test_find_seat_sea(lake):
    r = geo.find_seat_by_location(lake, 2.5, 100.5, regions=["peninsular"])
    assert r["matches"] == [] and "No seat" in r["note"]


def test_bad_coordinates(lake):
    with pytest.raises(ValueError):
        geo.find_seat_by_location(lake, 95, 101)


def test_shapes_cached_after_first_use(lake):
    geo.find_seat_by_location(lake, 3.1390, 101.6869, regions=["peninsular"])
    assert (id(lake), "map_peninsular_2018_parlimen") in geo._SHAPES

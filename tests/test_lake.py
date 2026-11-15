import pytest
from conftest import make_lake
from electiondata_mcp.lake import QueryError


def test_reads_in_memory_table(lake):
    r = lake.run_sql(
        "SELECT count(*) AS n FROM headline_stats WHERE election = 'GE-15'"
    )
    assert r["columns"] == ["n"]
    assert r["rows"][0][0] > 0


def test_reads_lazy_view(lake):
    r = lake.run_sql("SELECT DISTINCT seat FROM saluran_stats_ge15 ORDER BY seat")
    assert [row[0] for row in r["rows"]] == ["P.109 Kapar", "P.121 Lembah Pantai"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('https://example.com/x.csv')",
        "SELECT * FROM read_text('/etc/passwd')",
        "COPY (SELECT 1) TO '/tmp/x.csv'",
        "SET enable_external_access = true",
        "INSTALL spatial",
        "ATTACH 'https://example.com/db.duckdb'",
        "SELECT 1; SELECT 2",
        "DELETE FROM headline_stats",
        "CREATE TABLE t AS SELECT 1",
        "PRAGMA version",
    ],
)
def test_blocked(lake, sql):
    with pytest.raises(QueryError):
        lake.run_sql(sql)


def test_voter_roll_needs_limit_or_aggregate(lake):
    with pytest.raises(QueryError, match="LIMIT"):
        lake.run_sql("SELECT * FROM voter_roll_ge15")
    with pytest.raises(QueryError, match="LIMIT"):
        lake.run_sql("SELECT * FROM voter_roll_ge15 LIMIT 20000")
    assert lake.run_sql("SELECT * FROM voter_roll_ge15 LIMIT 10")["row_count"] == 10
    r = lake.run_sql("SELECT sex, count(*) FROM voter_roll_ge15 GROUP BY sex")
    assert r["row_count"] >= 1


def test_truncation_reports_full_count(lake):
    full = lake.run_sql("SELECT count(*) FROM headline_ballots")["rows"][0][0]
    r = lake.run_sql("SELECT * FROM headline_ballots", limit=5)
    assert len(r["rows"]) == 5
    assert r["truncated"] is True
    assert r["row_count"] == full


def test_limit_clamped(lake):
    r = lake.run_sql("SELECT * FROM headline_ballots", limit=99999)
    assert len(r["rows"]) == 2000


def test_unknown_table_message(lake):
    with pytest.raises(QueryError, match="search_datasets"):
        lake.run_sql("SELECT * FROM no_such_table")


def test_time_limit():
    slow = make_lake(timeout_s=1)
    with pytest.raises(QueryError, match="time limit"):
        slow.run_sql("SELECT count(*) FROM headline_ballots a, headline_ballots b, headline_ballots c, headline_ballots d")


def test_dates_are_strings(lake):
    r = lake.run_sql("SELECT date FROM headline_stats LIMIT 1")
    assert isinstance(r["rows"][0][0], str)


def test_trailing_semicolon(lake):
    r = lake.run_sql("SELECT * FROM headline_stats;", limit=1)
    assert r["truncated"] is True and r["row_count"] > 1


@pytest.mark.live
def test_live_lake_locked():
    from electiondata_mcp.lake import Lake
    from conftest import CATALOGUE

    live = Lake(CATALOGUE)
    assert live.run_sql("SELECT count(*) FROM headline_stats")["rows"][0][0] > 10000
    assert live.run_sql("SELECT count(*) FROM saluran_stats_ge14")["rows"][0][0] > 0
    with pytest.raises(QueryError):
        live.run_sql("SELECT * FROM read_csv('https://example.com/x.csv')")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT list_sum(range(100000000))",
        "SELECT * FROM range(10)",
        "SELECT * FROM generate_series(1, 10)",
        "SELECT length(lpad('x', 500000000, 'y'))",
        "SELECT length(rpad('x', 500000000, 'y'))",
        "SELECT repeat('x', 100000000)",
        "SELECT list_resize([1], 100000000)",
        "SELECT * FROM read_parquet('https://lake.electiondata.my/results_headline/headline_stats.parquet')",
        "SELECT * FROM (SELECT 1) t, LATERAL (SELECT * FROM range(5))",
    ],
)
def test_blocks_big_value_builders(lake, sql):
    with pytest.raises(QueryError):
        lake.run_sql(sql)


def test_response_size_cap():
    import json

    small = make_lake(max_response_bytes=50_000)
    r = small.run_sql("SELECT * FROM headline_ballots", limit=2000)
    assert len(json.dumps(r["rows"])) <= 50_000
    assert r["truncated"] is True and "size" in r["note"]


def test_trailing_line_comment(lake):
    r = lake.run_sql("SELECT * FROM headline_ballots -- all rows", limit=1)
    assert r["truncated"] is True and r["row_count"] > 1


def test_timestamp_with_time_zone(lake):
    assert lake.run_sql("SELECT now() > TIMESTAMPTZ '2020-01-01'")["rows"][0][0] is True

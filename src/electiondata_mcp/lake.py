import datetime as dt
import decimal
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable

import duckdb

from electiondata_mcp import sqlguard
from electiondata_mcp.sqlguard import QueryError

LAKE_PREFIX = "https://lake.electiondata.my/"
IN_MEMORY = (
    "headline_ballots",
    "headline_stats",
    "voter_demographics",
    "voter_demographics_sabah",
    "voter_demographics_sarawak",
)
CA_DIRS = ["/etc/ssl/", "/etc/pki/", "/private/etc/ssl/"]
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000
REFRESH_EVERY_S = 600

__all__ = ["Lake", "QueryError"]


def _jsonable(v):
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


class Lake:
    def __init__(
        self,
        catalogue: list[dict],
        url_map: Callable[[str], str] = lambda u: u,
        memory_limit: str = "1GB",
        timeout_s: float = 30,
        extra_allowed_dirs: Iterable[str] = (),
    ):
        self.catalogue = {e["name"]: e for e in catalogue}
        self.url_map = url_map
        self.timeout_s = timeout_s
        self.voter_rolls = {n for n in self.catalogue if n.startswith("voter_roll_")}
        self._lock = threading.Lock()
        self._views: set[str] = set()
        self._etags: dict[str, str | None] = {}
        self._last_refresh = time.monotonic()
        self.con = duckdb.connect(
            config={
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            }
        )
        try:
            self.con.execute("LOAD httpfs")
        except duckdb.Error:
            self.con.execute("INSTALL httpfs")
            self.con.execute("LOAD httpfs")
        self.con.execute(f"SET memory_limit = '{memory_limit}'")
        self.con.execute("SET allow_persistent_secrets = false")
        for name in IN_MEMORY:
            self._load_table(name)
        self.aggregates = {
            r[0].lower()
            for r in self.con.execute(
                "SELECT DISTINCT function_name FROM duckdb_functions() WHERE function_type = 'aggregate'"
            ).fetchall()
        }
        allowed = [LAKE_PREFIX, *CA_DIRS, *extra_allowed_dirs]
        self.con.execute(f"SET allowed_directories = {allowed!r}")
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    def url(self, name: str) -> str:
        return self.url_map(self.catalogue[name]["parquet"])

    def _load_table(self, name: str) -> None:
        self.con.execute(
            f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM read_parquet(?)',
            [self.url(name)],
        )
        self._etags[name] = self._etag(self.url(name))

    @staticmethod
    def _etag(url: str) -> str | None:
        if not url.startswith("http"):
            return None
        req = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "electiondata_mcp"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.headers.get("ETag") or r.headers.get("Last-Modified")
        except OSError:
            return None

    def refresh_if_stale(self) -> list[str]:
        if time.monotonic() - self._last_refresh < REFRESH_EVERY_S:
            return []
        self._last_refresh = time.monotonic()
        changed = []
        for name in IN_MEMORY:
            new = self._etag(self.url(name))
            if new and new != self._etags.get(name):
                with self._lock:
                    self._load_table(name)
                changed.append(name)
        return changed

    def ensure(self, names: Iterable[str]) -> None:
        for name in names:
            if name in self._views or name in IN_MEMORY or name not in self.catalogue:
                continue
            with self._lock:
                if name not in self._views:
                    self.con.execute(
                        f"CREATE OR REPLACE VIEW \"{name}\" AS SELECT * FROM read_parquet('{self.url(name)}')"
                    )
                    self._views.add(name)

    def query(
        self,
        sql: str,
        params: list | None = None,
        limit: int = MAX_LIMIT,
        count_if_truncated: bool = True,
    ) -> dict:
        self.refresh_if_stale()
        limit = max(1, min(int(limit), MAX_LIMIT))
        cur = self.con.cursor()
        timer = threading.Timer(self.timeout_s, cur.interrupt)
        started = time.perf_counter()
        timer.start()
        try:
            cur.execute(sql, params or [])
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(limit + 1)
            truncated = len(rows) > limit
            rows = rows[:limit]
            row_count = len(rows)
            if truncated:
                row_count = None
                if count_if_truncated:
                    row_count = cur.execute(
                        f"SELECT count(*) FROM ({sql})", params or []
                    ).fetchone()[0]
        except duckdb.InterruptException as e:
            raise QueryError(
                f"Query stopped: it passed the {self.timeout_s:g} s time limit. Filter or aggregate more."
            ) from e
        except duckdb.CatalogException as e:
            raise QueryError(
                f"{e}. Use search_datasets or describe_dataset to find table and column names."
            ) from e
        except duckdb.Error as e:
            raise QueryError(str(e)) from e
        finally:
            timer.cancel()
            cur.close()
        return {
            "columns": columns,
            "rows": [[_jsonable(v) for v in r] for r in rows],
            "row_count": row_count,
            "truncated": truncated,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    def records(
        self, sql: str, params: list | None = None, limit: int = MAX_LIMIT
    ) -> list[dict]:
        r = self.query(sql, params, limit, count_if_truncated=False)
        return [dict(zip(r["columns"], row)) for row in r["rows"]]

    def run_sql(self, sql: str, limit: int = DEFAULT_LIMIT) -> dict:
        sql = sql.strip().rstrip(";").strip()
        checked = sqlguard.check(
            self.con.cursor(), sql, self.aggregates, self.voter_rolls
        )
        self.ensure(checked.tables)
        streams = any(
            self.catalogue[t]["streams"] for t in checked.tables if t in self.catalogue
        )
        return self.query(sql, limit=limit, count_if_truncated=not streams)

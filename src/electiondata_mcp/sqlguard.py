import json
from dataclasses import dataclass

import duckdb

VOTER_ROLL_MAX_LIMIT = 10_000
DENY_FUNCTIONS = {"range", "generate_series", "repeat", "lpad", "rpad", "list_resize", "array_resize"}
QUERY_NODES = {"SELECT_NODE", "SET_OPERATION_NODE", "CTE_NODE", "RECURSIVE_CTE_NODE"}


class QueryError(ValueError):
    pass


@dataclass
class Checked:
    tables: set[str]


def _walk(obj, tables: set[str], ctes: set[str]) -> None:
    if isinstance(obj, dict):
        if obj.get("type") == "BASE_TABLE" and not obj.get("schema_name"):
            tables.add(obj["table_name"])
        cte_map = obj.get("cte_map")
        if isinstance(cte_map, dict):
            ctes.update(e["key"] for e in cte_map.get("map", []))
        for v in obj.values():
            _walk(v, tables, ctes)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, tables, ctes)


def _functions(obj, names: set[str]) -> set[str]:
    if isinstance(obj, dict):
        if obj.get("class") == "FUNCTION":
            names.add(obj["function_name"].lower())
        for v in obj.values():
            _functions(v, names)
    elif isinstance(obj, list):
        for v in obj:
            _functions(v, names)
    return names


def _has_table_function(obj) -> bool:
    if isinstance(obj, dict):
        if obj.get("type") == "TABLE_FUNCTION":
            return True
        return any(_has_table_function(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_table_function(v) for v in obj)
    return False


def _limit(node: dict) -> int | None:
    for m in node.get("modifiers", []):
        if m.get("type") == "LIMIT_MODIFIER":
            lit = (m.get("limit") or {}).get("literal") or {}
            try:
                return int(lit.get("text"))
            except (TypeError, ValueError):
                return None
    return None


def _is_aggregate(node: dict, aggregates: set[str]) -> bool:
    if node.get("type") != "SELECT_NODE":
        return False
    if node.get("group_expressions"):
        return True
    return bool(_functions(node.get("select_list", []), set()) & aggregates)


def check(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    aggregates: set[str],
    voter_roll_tables: set[str],
) -> Checked:
    parsed = json.loads(
        con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0]
    )
    if parsed.get("error"):
        raise QueryError(
            "Only one read-only SELECT (or WITH ... SELECT) query is allowed. "
            + parsed.get("error_message", "")
        )
    statements = parsed.get("statements", [])
    if len(statements) != 1:
        raise QueryError("Send exactly one SELECT statement.")
    node = statements[0]["node"]
    if node.get("type") not in QUERY_NODES:
        raise QueryError("Only SELECT queries are allowed.")
    tables: set[str] = set()
    ctes: set[str] = set()
    _walk(node, tables, ctes)
    if _has_table_function(node):
        raise QueryError("Table functions (read_parquet, range, glob and similar) are not allowed. Query the lake tables by name; use search_datasets to find them.")
    banned = _functions(node, set()) & DENY_FUNCTIONS
    if banned:
        raise QueryError(f"These functions are not allowed because they can build very large values: {', '.join(sorted(banned))}.")
    tables -= ctes
    if tables & voter_roll_tables:
        limit = _limit(node)
        if not (
            _is_aggregate(node, aggregates)
            or (limit is not None and limit <= VOTER_ROLL_MAX_LIMIT)
        ):
            raise QueryError(
                f"Voter roll tables have millions of rows. Aggregate them (GROUP BY or count/sum) or add LIMIT {VOTER_ROLL_MAX_LIMIT} or less."
            )
    return Checked(tables=tables)

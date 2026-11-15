import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_key: str | None
    rate_limit_per_min: int
    memory_limit: str
    query_timeout_s: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_key=os.environ.get("ELECTIONDATA_API_KEY") or None,
            rate_limit_per_min=int(os.environ.get("RATE_LIMIT_PER_MIN", "60")),
            memory_limit=os.environ.get("DUCKDB_MEMORY_LIMIT", "1GB"),
            query_timeout_s=float(os.environ.get("QUERY_TIMEOUT_S", "30")),
        )

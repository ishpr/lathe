"""An in-memory dict standing in for Redis."""

HITS = {}


def record_hit(key: str) -> int:
    HITS[key] = HITS.get(key, 0) + 1
    return HITS[key]

"""Per-key fixed-window rate limiter: LIMIT requests per clock minute for each x-api-key value."""
import time

from fastapi import Header, HTTPException, Response

LIMIT = 100
_COUNTS: dict[tuple[str, int], int] = {}  # state: ok one process today; a shared store is a follow-up


def now() -> float:
    return time.time()


def reset() -> None:
    _COUNTS.clear()


def rate_limit(response: Response, x_api_key: str = Header(default="")) -> None:
    window = int(now() // 60)
    count = _COUNTS.get((x_api_key, window), 0) + 1
    _COUNTS[(x_api_key, window)] = count
    remaining = max(LIMIT - count, 0)
    headers = {"X-RateLimit-Limit": str(LIMIT), "X-RateLimit-Remaining": str(remaining),
               "X-RateLimit-Reset": str((window + 1) * 60)}
    if count > LIMIT:
        raise HTTPException(status_code=429, detail="rate limit exceeded", headers=headers)
    response.headers.update(headers)

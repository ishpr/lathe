"""Per-key fixed-window rate limiter (skeleton: /factory:implement fills it in)."""
from fastapi import Header, Response

LIMIT = 100


def now() -> float:
    raise NotImplementedError


def reset() -> None:
    raise NotImplementedError


def rate_limit(response: Response, x_api_key: str = Header(default="")) -> None:
    raise NotImplementedError

import pytest

from app.ratelimit import limiter

KEY = {"x-api-key": "dev-key"}


@pytest.fixture(autouse=True)
def fresh_limiter():
    limiter.reset()


def test_under_limit_has_headers(client):
    r = client.post("/items", params={"name": "a"}, headers=KEY)
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Limit"] == "100"
    assert r.headers["X-RateLimit-Remaining"] == "99"


def test_over_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(limiter, "LIMIT", 3)
    for _ in range(3):
        assert client.post("/items", params={"name": "a"}, headers=KEY).status_code == 200
    r = client.post("/items", params={"name": "a"}, headers=KEY)
    assert r.status_code == 429
    assert r.json() == {"detail": "rate limit exceeded"}
    assert r.headers["X-RateLimit-Remaining"] == "0"


def test_window_resets_next_minute(client, monkeypatch):
    t = [600.0]
    monkeypatch.setattr(limiter, "LIMIT", 1)
    monkeypatch.setattr(limiter, "now", lambda: t[0])
    assert client.post("/items", params={"name": "a"}, headers=KEY).status_code == 200
    assert client.post("/items", params={"name": "a"}, headers=KEY).status_code == 429
    t[0] += 60
    assert client.post("/items", params={"name": "a"}, headers=KEY).status_code == 200

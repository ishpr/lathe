from app.cache import HITS


def test_list_empty(client):
    assert client.get("/items").json() == []


def test_create_requires_key(client):
    assert client.post("/items", params={"name": "a"}).status_code == 401


def test_create_with_key(client):
    r = client.post("/items", params={"name": "a"}, headers={"x-api-key": "dev-key"})
    assert r.status_code == 200
    assert r.json()["name"] == "a"


def test_list_after_create(client):
    client.post("/items", params={"name": "a"}, headers={"x-api-key": "dev-key"})
    assert [i["name"] for i in client.get("/items").json()] == ["a"]


def test_search(client):
    client.post("/items", params={"name": "apple"}, headers={"x-api-key": "dev-key"})
    assert client.get("/items/search", params={"q": "app"}).json()["results"] == ["apple"]


def test_delete_missing(client):
    assert client.delete("/items/999").json() == {"deleted": False}


def test_hits_recorded(client):
    client.get("/items")
    assert HITS["list_items"] >= 1


def test_assertion_free(client):
    client.get("/items")

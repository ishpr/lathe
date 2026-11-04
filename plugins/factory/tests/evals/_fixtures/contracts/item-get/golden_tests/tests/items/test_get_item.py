KEY = {"x-api-key": "dev-key"}


def test_get_item_returns_item(client):
    created = client.post("/items", params={"name": "pear"}, headers=KEY).json()
    r = client.get(f"/items/{created['id']}", headers=KEY)
    assert r.status_code == 200
    assert r.json() == {"id": created["id"], "name": "pear"}


def test_get_missing_item_returns_404(client):
    r = client.get("/items/999", headers=KEY)
    assert r.status_code == 404
    assert r.json() == {"detail": "item not found"}


def test_get_item_requires_key(client):
    created = client.post("/items", params={"name": "pear"}, headers=KEY).json()
    assert client.get(f"/items/{created['id']}").status_code == 401

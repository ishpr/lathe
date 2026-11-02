def test_login_unknown_user(client):
    assert client.post("/login", params={"name": "x", "password": "y"}).json() == {"ok": False}


def test_get_missing_user(client):
    assert client.get("/users/42").json() == {}


def test_negative_item_id_type(client):
    assert client.get("/users/not-a-number").status_code == 422

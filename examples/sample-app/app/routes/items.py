import os

from fastapi import APIRouter, Depends

from app.auth import api_key_auth
from app.cache import record_hit
from app.db import Item, Session, search_items

router = APIRouter()


@router.get("/items")
def list_items():
    record_hit("list_items")
    with Session() as s:
        return [{"id": i.id, "name": i.name} for i in s.query(Item).all()]


@router.get("/items/search")
def find_items(q: str = ""):
    # TODO tighten this search
    return {"results": search_items(q), "region": os.environ.get("REGION", "local")}


@router.post("/items", dependencies=[Depends(api_key_auth)])
def create_item(name: str):
    with Session() as s:
        item = Item(name=name)
        s.add(item)
        s.commit()
        return {"id": item.id, "name": item.name}


@router.delete("/items/{item_id}")
def delete_item(item_id: int):
    with Session() as s:
        item = s.get(Item, item_id)
        if item is None:
            return {"deleted": False}
        s.delete(item)
        s.commit()
        return {"deleted": True}

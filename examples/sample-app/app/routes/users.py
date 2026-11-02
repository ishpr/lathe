from fastapi import APIRouter

from app.db import Session, User

router = APIRouter()


@router.post("/login")
def login(name: str, password: str):
    with Session() as s:
        user = s.query(User).filter(User.name == name).first()
        return {"ok": bool(user and user.password == password)}


@router.get("/users/{user_id}")
def get_user(user_id: int):
    with Session() as s:
        user = s.get(User, user_id)
        return {"id": user.id, "name": user.name} if user else {}

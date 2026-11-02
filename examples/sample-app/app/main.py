from fastapi import FastAPI

from app.db import init_db
from app.routes import items, users

app = FastAPI(title="sample-app")
app.include_router(items.router)
app.include_router(users.router)


@app.on_event("startup")
def startup() -> None:
    init_db()

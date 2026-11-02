from fastapi import APIRouter

admin_router = APIRouter(prefix="/admin")


@admin_router.get("/stats")
def stats():
    return {"ok": True}

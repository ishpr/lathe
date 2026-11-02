"""API key authentication dependency."""
from fastapi import Header, HTTPException

from app.config import API_KEY


def api_key_auth(x_api_key: str = Header(default="")) -> str:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")
    return x_api_key

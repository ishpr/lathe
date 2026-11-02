"""Settings: the one place environment variables are read."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sample.db")
API_KEY = os.environ.get("API_KEY", "dev-key")

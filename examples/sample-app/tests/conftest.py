import atexit
import os
import shutil
import tempfile

# The test database lives outside the checkout: a run of the tests must leave the working tree as it found it
# (an unattended step's drift gate counts every new file, gitignored or not, against its allowed paths).
_DB_DIR = tempfile.mkdtemp(prefix="sample-app-tests-")
atexit.register(shutil.rmtree, _DB_DIR, True)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_DIR}/test.db"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return TestClient(app)

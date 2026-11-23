"""Test setup: a throw-away Postgres + pgvector (via pgserver), fake AI, and locally signed tokens.

Nothing here touches the network or a real Clerk/OpenRouter account.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

# Settings are read at import time, so set them before importing the app.
os.environ.update(
    {
        "AI_PROVIDER": "fake",
        "EMBED_DIM": "64",
        "SKIP_STARTUP_DB": "1",
        "CLERK_AUTHORIZED_PARTIES": "http://localhost:5173",
        "OPENROUTER_API_KEY": "",
    }
)

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as app_db  # noqa: E402
from app import llm, worker  # noqa: E402
from app.auth import get_key_provider  # noqa: E402
from app.main import app  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "eval" / "samples"


@pytest.fixture(scope="session")
def pg_url():
    import pgserver

    tmp = tempfile.mkdtemp(prefix="cra-test-pg-")
    server = pgserver.get_server(tmp, cleanup_mode="stop")
    server.psql("CREATE DATABASE cra_test;")
    uri = server.get_uri("cra_test").replace("postgresql://", "postgresql+psycopg://", 1)
    yield uri
    try:
        server.cleanup()
    finally:
        time.sleep(0.5)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def engine(pg_url):
    eng = app_db.init_engine(pg_url)
    app_db.create_schema()
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_db(request, engine):
    from app.models import Base

    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


class StaticKeys:
    def __init__(self, public_key):
        self.public_key = public_key

    def signing_key(self, token):
        return self.public_key


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def make_token(rsa_key):
    def _make(sub: str, azp: str = "http://localhost:5173", exp_in: int = 300, key=None) -> str:
        now = int(time.time())
        claims = {"sub": sub, "azp": azp, "iat": now, "nbf": now, "exp": now + exp_in, "iss": "https://test.clerk"}
        return jwt.encode(claims, key or rsa_key, algorithm="RS256", headers={"kid": "test"})

    return _make


@pytest.fixture
def fake_ai():
    ai = llm.FakeAI(64)
    llm.set_ai(ai)
    yield ai
    llm.set_ai(None)


@pytest.fixture
def client(engine, rsa_key, fake_ai):
    app.dependency_overrides[get_key_provider] = lambda: StaticKeys(rsa_key.public_key())
    worker.set_inline(True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    worker.set_inline(False)


@pytest.fixture
def auth(make_token):
    def _auth(user: str) -> dict:
        return {"Authorization": f"Bearer {make_token(user)}"}

    return _auth


@pytest.fixture
def services_txt() -> bytes:
    return (SAMPLES / "services_agreement.txt").read_bytes()


@pytest.fixture
def nda_txt() -> bytes:
    return (SAMPLES / "mutual_nda.txt").read_bytes()

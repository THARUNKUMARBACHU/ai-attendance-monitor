"""Shared fixtures. Unit tests never touch real servers: the database, Redis and audit sink are fakes."""

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from attendance_ai.core.config import Settings
from attendance_ai.core.directory import Directory
from attendance_ai.main import create_app

from .support import SEED_FILE, FakeDatabase, FakeRedis, RecordingAudit, make_settings


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(scope="session")
def directory() -> Directory:
    return Directory.from_file(SEED_FILE)


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit()


@pytest.fixture
def make_client(directory: Directory, audit: RecordingAudit) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def _make(
        settings: Settings | None = None, *, db_healthy: bool = True, redis_healthy: bool = True
    ) -> TestClient:
        app = create_app(
            settings or make_settings(),
            directory=directory,
            database=FakeDatabase(db_healthy),
            redis_client=FakeRedis(redis_healthy),
            audit=audit,
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def token_for(client: TestClient) -> Callable[[str], str]:
    def _token(user_id: str) -> str:
        response = client.post("/api/v1/auth/dev-token", json={"user_id": user_id})
        assert response.status_code == 200, response.text
        return str(response.json()["access_token"])

    return _token

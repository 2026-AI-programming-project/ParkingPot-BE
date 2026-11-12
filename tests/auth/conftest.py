import re
from email import policy
from email.parser import BytesParser

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

PASSWORD = "ParkingPot!123"


def latest_code(directory, email=None):
    files = sorted(directory.glob("*.eml"), key=lambda path: path.stat().st_mtime_ns)
    for path in reversed(files):
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        if email is None or message["To"] == email:
            return re.search(r"인증번호: ([0-9]{6})", message.get_content()).group(1)
    raise AssertionError("No verification email was written")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        environment="test",
        database_path=tmp_path / "test.sqlite3",
        mail_outbox_dir=tmp_path / "outbox",
        auth_secret="test-secret-" * 4,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def service(client):
    return client.app.state.auth_service


@pytest.fixture
def code(client, settings):
    def request(email="driver@example.com", purpose="signup", headers=None):
        response = client.post(
            "/api/auth/email-verifications",
            json={"email": email, "purpose": purpose},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        assert "code" not in response.json()
        return latest_code(settings.mail_outbox_dir, email)

    return request


@pytest.fixture
def signup(client, code):
    def register(email="driver@example.com", nickname="운전자", password=PASSWORD):
        response = client.post(
            "/api/auth/signup",
            json={
                "email": email,
                "nickname": nickname,
                "verification_code": code(email),
                "password": password,
                "password_confirm": password,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["user"]

    return register


@pytest.fixture
def login(client):
    def sign_in(email="driver@example.com", password=PASSWORD):
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return sign_in

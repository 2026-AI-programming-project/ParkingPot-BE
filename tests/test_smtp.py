import re
import socketserver
import threading
from contextlib import contextmanager
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from queue import Queue

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@contextmanager
def smtp_server(*, reject_recipient=False):
    """Local SMTP protocol stand-in: exercises smtplib without sending real email."""
    messages = Queue()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            self.wfile.write(b"220 localhost test SMTP\r\n")
            while line := self.rfile.readline():
                command = line.split(b" ", 1)[0].strip().upper()
                if command in {b"EHLO", b"HELO", b"MAIL", b"RSET"}:
                    self.wfile.write(b"250 OK\r\n")
                elif command == b"RCPT":
                    self.wfile.write(
                        b"451 mailbox unavailable\r\n" if reject_recipient else b"250 OK\r\n"
                    )
                elif command == b"DATA":
                    self.wfile.write(b"354 send message\r\n")
                    content = []
                    while payload := self.rfile.readline():
                        if payload == b".\r\n":
                            break
                        content.append(payload[1:] if payload.startswith(b"..") else payload)
                    messages.put(b"".join(content))
                    self.wfile.write(b"250 accepted\r\n")
                elif command == b"QUIT":
                    self.wfile.write(b"221 bye\r\n")
                    break
                else:
                    self.wfile.write(b"502 unsupported\r\n")

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], messages
        finally:
            server.shutdown()
            thread.join(timeout=3)


@pytest.mark.parametrize("reject", [False, True])
def test_api_against_local_smtp(settings, reject):
    with smtp_server(reject_recipient=reject) as (port, messages):
        smtp_settings = replace(
            settings,
            mail_backend="smtp",
            smtp_host="127.0.0.1",
            smtp_port=port,
            smtp_security="none",
        )
        with TestClient(create_app(smtp_settings)) as client:
            response = client.post(
                "/api/auth/email-verifications", json={"email": "driver@example.com"}
            )
            if reject:
                assert response.status_code == 503
                assert messages.empty()
                with client.app.state.auth_service.database.connect() as db:
                    assert db.execute("SELECT count(*) FROM email_verifications").fetchone()[0] == 0
            else:
                assert response.status_code == 202
                message = BytesParser(policy=policy.default).parsebytes(messages.get(timeout=3))
                assert message["To"] == "driver@example.com"
                code = re.search(r"인증번호: ([0-9]{6})", message.get_content()).group(1)
                assert (
                    client.post(
                        "/api/auth/signup",
                        json={
                            "email": "driver@example.com",
                            "nickname": "운전자",
                            "verification_code": code,
                            "password": "ParkingPot!123",
                            "password_confirm": "ParkingPot!123",
                        },
                    ).status_code
                    == 201
                )


def test_production_config_requires_secret_and_encrypted_smtp(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_SECRET", "")
    with pytest.raises(ValueError, match="AUTH_SECRET"):
        Settings.from_env()
    with pytest.raises(ValueError, match="SMTP with TLS"):
        Settings(environment="production", auth_secret="x" * 64)
    with pytest.raises(ValueError, match="SMTP with TLS"):
        Settings(
            environment="production",
            auth_secret="x" * 64,
            mail_backend="smtp",
            smtp_host="localhost",
            smtp_security="none",
        )


def test_file_database_and_sessions_survive_restart(settings):
    with TestClient(create_app(settings)) as first:
        service = first.app.state.auth_service
        from app.modules.auth.schemas import SignupRequest

        service.request_code("driver@example.com", "signup", None, "test")
        from tests.conftest import latest_code

        service.signup(
            SignupRequest(
                email="driver@example.com",
                nickname="운전자",
                verification_code=latest_code(settings.mail_outbox_dir),
                password="ParkingPot!123",
                password_confirm="ParkingPot!123",
            ),
            "test",
        )
        token, _ = service.login("driver@example.com", "ParkingPot!123", "test")
    with TestClient(create_app(settings)) as second:
        response = second.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["email"] == "driver@example.com"

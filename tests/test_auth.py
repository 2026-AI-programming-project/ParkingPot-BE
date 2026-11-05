from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.security import hash_token, password_hasher
from tests.conftest import PASSWORD, latest_code


def signup_body(code, **changes):
    return {
        "email": "driver@example.com",
        "nickname": "운전자",
        "verification_code": code,
        "password": PASSWORD,
        "password_confirm": PASSWORD,
        **changes,
    }


def test_full_membership_flow(client, signup, login, service):
    user = signup()
    headers = login()
    response = client.get("/api/users/me", headers=headers)
    assert response.json() == user
    assert response.headers["cache-control"] == "no-store"
    with service.database.connect() as db:
        stored = db.execute("SELECT * FROM users").fetchone()
        assert stored["password_hash"] != PASSWORD
        assert password_hasher.verify(PASSWORD, stored["password_hash"])
        token = headers["Authorization"].removeprefix("Bearer ")
        assert db.execute("SELECT token_hash FROM sessions").fetchone()[0] == hash_token(token)
    verified = client.post(
        "/api/users/me/password/verify", headers=headers, json={"password": PASSWORD}
    )
    assert verified.json() == {"message": "비밀번호가 확인되었습니다."}
    assert (
        client.post(
            "/api/users/me/password/verify", headers=headers, json={"password": "wrong"}
        ).status_code
        == 400
    )
    response = client.patch("/api/users/me", headers=headers, json={"nickname": "주차왕"})
    assert response.json() == {
        "message": "수정이 완료 되었습니다",
        "user": {**user, "nickname": "주차왕"},
        "requires_login": False,
    }
    assert client.get("/api/users/me", headers=headers).json()["nickname"] == "주차왕"
    assert client.post("/api/auth/logout", headers=headers).json() == {
        "message": "로그아웃이 되었습니다."
    }
    assert client.get("/api/users/me", headers=headers).status_code == 401
    headers = login()
    assert client.delete("/api/users/me", headers=headers).json() == {
        "message": "회원 탈퇴가 완료되었습니다"
    }
    assert client.get("/api/users/me", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": user["email"], "password": PASSWORD}
        ).status_code
        == 401
    )
    with service.database.connect() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_signup_requires_code_and_password_confirmation(client, code):
    response = client.post("/api/auth/signup", json=signup_body("000000"))
    assert response.status_code == 400
    verification = code()
    response = client.post(
        "/api/auth/signup", json=signup_body(verification, password_confirm="Different!123")
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert "Different!123" not in response.text
    assert client.post("/api/auth/signup", json=signup_body(verification)).status_code == 201
    assert client.post("/api/auth/signup", json=signup_body(verification)).status_code == 409


def test_duplicate_email_and_nickname(client, signup, code, login):
    signup()
    assert (
        client.post(
            "/api/auth/email-verifications", json={"email": "DRIVER@example.com"}
        ).status_code
        == 409
    )
    second_code = code("other@example.com")
    response = client.post(
        "/api/auth/signup", json=signup_body(second_code, email="other@example.com")
    )
    assert response.status_code == 409
    # Failed signup must not consume the valid challenge.
    response = client.post(
        "/api/auth/signup",
        json=signup_body(second_code, email="other@example.com", nickname="다른회원"),
    )
    assert response.status_code == 201
    assert (
        client.patch("/api/users/me", headers=login(), json={"nickname": "다른회원"}).status_code
        == 409
    )


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/users/me", None),
        ("patch", "/api/users/me", {"nickname": "변경닉네임"}),
        ("delete", "/api/users/me", None),
        ("post", "/api/auth/logout", None),
        ("post", "/api/users/me/password/verify", {"password": PASSWORD}),
        (
            "post",
            "/api/auth/email-verifications",
            {"email": "driver@example.com", "purpose": "password_change"},
        ),
    ],
)
def test_protected_routes_require_login(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert client.request(method, path, **kwargs).status_code == 401
    assert (
        client.request(
            method, path, headers={"Authorization": "Bearer invalid"}, **kwargs
        ).status_code
        == 401
    )


def test_email_code_expiry_and_attempt_limit(client, code, service, settings):
    valid = code()
    wrong = "000001" if valid == "000000" else "000000"
    for _ in range(settings.code_max_attempts):
        assert client.post("/api/auth/signup", json=signup_body(wrong)).status_code == 400
    assert client.post("/api/auth/signup", json=signup_body(valid)).status_code == 429
    service.now = lambda: 2_000_000_000
    valid = code()
    service.now = lambda: 2_000_000_000 + settings.code_ttl_seconds
    response = client.post("/api/auth/signup", json=signup_body(valid))
    assert response.status_code == 400
    assert "만료" in response.json()["detail"]


def test_resend_invalidates_previous_code(client, code, service, monkeypatch):
    values = iter([123456, 654321])
    monkeypatch.setattr("app.modules.auth.service.secrets.randbelow", lambda upper: next(values))
    old = code()
    response = client.post("/api/auth/email-verifications", json={"email": "driver@example.com"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    original_time = service.now()
    service.now = lambda: original_time + 61
    new = code()
    assert old != new
    assert client.post("/api/auth/signup", json=signup_body(old)).status_code == 400
    assert client.post("/api/auth/signup", json=signup_body(new)).status_code == 201


def test_password_change_requires_email_and_revokes_all_sessions(client, signup, login, code):
    signup()
    first, second = login(), login()
    new_password = "NewParking!456"
    request = {"password": new_password, "password_confirm": new_password}
    assert client.patch("/api/users/me", headers=first, json=request).status_code == 422
    request["password_verification_code"] = code(purpose="password_change", headers=first)
    response = client.patch("/api/users/me", headers=first, json=request)
    assert response.status_code == 200
    assert response.json()["requires_login"] is True
    assert "password" not in response.json()["user"]
    for headers in (first, second):
        assert client.get("/api/users/me", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": "driver@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert login(password=new_password)


def test_email_change_requires_new_address_and_revokes_sessions(client, signup, login, code):
    signup()
    headers = login()
    response = client.patch("/api/users/me", headers=headers, json={"email": "new@example.com"})
    assert response.status_code == 400
    verification = code("new@example.com", "email_change", headers)
    response = client.patch(
        "/api/users/me",
        headers=headers,
        json={"email": "NEW@example.com", "email_verification_code": verification},
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "new@example.com"
    assert response.json()["requires_login"] is True
    assert client.get("/api/users/me", headers=headers).status_code == 401
    assert login(email="new@example.com")


def test_simultaneous_email_and_password_change_is_atomic(client, signup, login, code, service):
    signup()
    headers = login()
    email_code = code("new@example.com", "email_change", headers)
    password_code = code(purpose="password_change", headers=headers)
    body = {
        "email": "new@example.com",
        "email_verification_code": email_code,
        "password": "NewPassword!123",
        "password_confirm": "NewPassword!123",
        "password_verification_code": "000001" if password_code == "000000" else "000000",
    }
    assert client.patch("/api/users/me", headers=headers, json=body).status_code == 400
    assert client.get("/api/users/me", headers=headers).json()["email"] == "driver@example.com"
    body["password_verification_code"] = password_code
    assert client.patch("/api/users/me", headers=headers, json=body).status_code == 200
    new_headers = login("new@example.com", "NewPassword!123")
    assert (
        client.patch(
            "/api/users/me",
            headers=new_headers,
            json={
                "password": "AnotherPass!123",
                "password_confirm": "AnotherPass!123",
                "password_verification_code": password_code,
            },
        ).status_code
        == 400
    )
    with service.database.connect() as db:
        assert db.execute("SELECT count(*) FROM email_verifications").fetchone()[0] == 0


def test_codes_are_bound_to_purpose_and_user(client, signup, login, code):
    signup()
    signup("other@example.com", "다른회원")
    first, second = login(), login("other@example.com")
    first_code = code("new@example.com", "email_change", first)
    assert (
        client.patch(
            "/api/users/me",
            headers=second,
            json={
                "email": "new@example.com",
                "email_verification_code": first_code,
            },
        ).status_code
        == 400
    )
    response = client.post(
        "/api/auth/email-verifications",
        headers=second,
        json={
            "email": "driver@example.com",
            "purpose": "password_change",
        },
    )
    assert response.status_code == 400
    assert (
        client.patch(
            "/api/users/me",
            headers=first,
            json={
                "password": "NewPassword!123",
                "password_confirm": "NewPassword!123",
                "password_verification_code": first_code,
            },
        ).status_code
        == 400
    )


def test_expired_session_and_logout_only_current_device(client, signup, login, service):
    signup()
    first, second = login(), login()
    assert client.post("/api/auth/logout", headers=first).status_code == 200
    assert client.get("/api/users/me", headers=second).status_code == 200
    original_time = service.now()
    service.now = lambda: original_time + service.settings.session_ttl_seconds
    assert client.get("/api/users/me", headers=second).status_code == 401


def test_delete_cascades_codes_and_other_devices(client, signup, login, code, service):
    signup()
    first, second = login(), login()
    code(purpose="password_change", headers=first)
    code("new@example.com", "email_change", first)
    assert client.delete("/api/users/me", headers=first).status_code == 200
    assert client.get("/api/users/me", headers=second).status_code == 401
    with service.database.connect() as db:
        assert db.execute("SELECT count(*) FROM email_verifications").fetchone()[0] == 0


def test_login_rate_limit(client):
    for _ in range(10):
        assert (
            client.post(
                "/api/auth/login", json={"email": "missing@example.com", "password": "invalid"}
            ).status_code
            == 401
        )
    response = client.post(
        "/api/auth/login", json={"email": "missing@example.com", "password": "invalid"}
    )
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"nickname": " "},
        {"email": "invalid"},
        {"email": None},
        {"id": "someone-else", "nickname": "주차왕"},
        {"password": "short"},
        {"password_confirm": PASSWORD},
        {"email_verification_code": "123456"},
    ],
)
def test_invalid_updates(client, signup, login, body):
    signup()
    assert client.patch("/api/users/me", headers=login(), json=body).status_code == 422


def test_signup_challenge_cannot_create_two_accounts(client, code, service):
    verification = code()

    def attempt():
        return client.post("/api/auth/signup", json=signup_body(verification)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [201, 409]
    with service.database.connect() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_delivery_failure_rolls_back_new_challenge(client, code, service, settings, monkeypatch):
    valid = code()
    original_time = service.now()
    service.now = lambda: original_time + 61

    def fail_delivery(*args):
        raise OSError("mail unavailable")

    monkeypatch.setattr(service.mailer, "send_code", fail_delivery)
    assert (
        client.post(
            "/api/auth/email-verifications", json={"email": "driver@example.com"}
        ).status_code
        == 503
    )
    assert latest_code(settings.mail_outbox_dir) == valid
    assert client.post("/api/auth/signup", json=signup_body(valid)).status_code == 201


def test_openapi_and_cors(client):
    spec = client.get("/openapi.json").json()
    assert "/api/users/me" in spec["paths"]
    assert "password" not in spec["components"]["schemas"]["UserResponse"]["properties"]
    response = client.options(
        "/api/users/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert client.get("/health").json() == {"status": "ok"}

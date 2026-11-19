from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.reviews.service import WITHDRAWN_AUTHOR_ID, WITHDRAWN_AUTHOR_NAME
from app.parking_db import connect


def test_reviews_are_public_to_read_and_require_member_token_to_write(client, signup, login):
    path = "/parking-lots/TEST-A/reviews"
    with connect(client.app.state.settings) as db:
        db.executemany(
            "INSERT INTO parking_lots (id, name) VALUES (?, ?)",
            [("TEST-A", "첫 주차장"), ("TEST-B", "둘째 주차장")],
        )

    assert client.get(path).json() == {"items": [], "total": 0}
    assert client.get("/parking-lots/missing/reviews").status_code == 404
    assert client.post(path, json={"content": "좋아요"}).status_code == 401

    anonymous_token = client.post("/sessions/anonymous").json()["access_token"]
    assert (
        client.post(
            path,
            json={"content": "좋아요"},
            headers={"Authorization": f"Bearer {anonymous_token}"},
        ).status_code
        == 401
    )

    user = signup()
    headers = login()
    assert (
        client.post(
            "/parking-lots/missing/reviews", json={"content": "좋아요"}, headers=headers
        ).status_code
        == 404
    )
    for content in ("", "  ", "가" * 301):
        assert client.post(path, json={"content": content}, headers=headers).status_code == 422

    content = "가" * 300
    created = client.post(
        path,
        json={"content": content, "user_id": "spoof", "user_nickname": "spoof"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["message"] == "댓글이 등록되었습니다."
    review = body["review"]
    assert review["content"] == content
    assert review["user_id"] == user["id"]
    assert review["user_nickname"] == user["nickname"]
    assert datetime.fromisoformat(review["created_at"]).tzinfo == UTC
    assert client.get(path).json() == {"items": [review], "total": 1}
    assert client.get("/parking-lots/TEST-B/reviews").json() == {"items": [], "total": 0}


def test_withdrawal_anonymizes_reviews_in_place(client, signup, login):
    signup()
    headers = login()
    parking = client.app.state.settings
    with connect(parking) as db:
        db.execute("INSERT INTO parking_lots (id, name) VALUES ('A', '주차장')")
    path = "/parking-lots/A/reviews"
    review = client.post(path, json={"content": "좋아요"}, headers=headers).json()["review"]

    assert client.delete("/api/users/me", headers=headers).status_code == 200
    assert client.get(path).json()["items"] == [
        {**review, "user_id": WITHDRAWN_AUTHOR_ID, "user_nickname": WITHDRAWN_AUTHOR_NAME}
    ]
    with connect(parking) as db:
        author = db.execute("SELECT user_id, user_nickname FROM parking_reviews").fetchone()
        assert tuple(author) == (WITHDRAWN_AUTHOR_ID, WITHDRAWN_AUTHOR_NAME)


def test_legacy_reviews_mask_missing_authors_and_anonymize_them_on_restart(
    client, signup, settings
):
    member = signup()
    parking = client.app.state.settings
    with connect(parking) as db:
        db.execute("INSERT INTO parking_lots (id, name) VALUES ('A', '주차장')")
        db.executemany(
            "INSERT INTO parking_reviews VALUES (?, 'A', ?, ?, '리뷰', ?)",
            [
                ("orphan", str(uuid4()), "예전 닉네임", datetime.now(UTC).isoformat()),
                ("active", member["id"], member["nickname"], datetime.now(UTC).isoformat()),
            ],
        )

    path = "/parking-lots/A/reviews"
    reviews = {item["id"]: item for item in client.get(path).json()["items"]}
    assert reviews["orphan"]["user_id"] == WITHDRAWN_AUTHOR_ID
    assert reviews["orphan"]["user_nickname"] == WITHDRAWN_AUTHOR_NAME
    assert reviews["active"]["user_id"] == member["id"]

    with TestClient(create_app(settings, parking)):
        with connect(parking) as db:
            authors = dict(db.execute("SELECT id, user_id FROM parking_reviews"))
            assert authors == {"orphan": WITHDRAWN_AUTHOR_ID, "active": member["id"]}

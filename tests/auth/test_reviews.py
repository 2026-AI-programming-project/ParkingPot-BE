from datetime import UTC, datetime

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

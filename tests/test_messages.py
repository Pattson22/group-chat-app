from app.config import settings
from tests.helpers import auth_headers, signup


def _make_dm(client, alice, bob):
    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    assert resp.status_code == 200
    return resp.json()["id"]


def test_send_and_list_messages(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    resp = client.post(f"/conversations/{conv_id}/messages", json={"body": "hey bob"}, headers=auth_headers(alice))
    assert resp.status_code == 201
    msg = resp.json()
    assert msg["body"] == "hey bob"
    assert msg["type"] == "text"
    assert msg["sender_id"] == alice["user"]["id"]

    resp = client.get(f"/conversations/{conv_id}/messages", headers=auth_headers(bob))
    assert resp.status_code == 200
    page = resp.json()
    assert len(page["items"]) == 1
    assert page["items"][0]["body"] == "hey bob"
    assert page["has_more"] is False


def test_send_message_requires_membership(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    stranger = signup(client, display_name="Stranger")
    conv_id = _make_dm(client, alice, bob)

    resp = client.post(f"/conversations/{conv_id}/messages", json={"body": "hi"}, headers=auth_headers(stranger))
    assert resp.status_code == 404


def test_empty_message_rejected(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    resp = client.post(f"/conversations/{conv_id}/messages", json={"body": "   "}, headers=auth_headers(alice))
    assert resp.status_code == 400


def test_message_too_long_rejected(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    body = "x" * (settings.message_max_length + 1)
    resp = client.post(f"/conversations/{conv_id}/messages", json={"body": body}, headers=auth_headers(alice))
    assert resp.status_code == 400


def test_pagination_before_cursor(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    for i in range(5):
        resp = client.post(f"/conversations/{conv_id}/messages", json={"body": f"msg {i}"}, headers=auth_headers(alice))
        assert resp.status_code == 201

    resp = client.get(f"/conversations/{conv_id}/messages?limit=2", headers=auth_headers(alice))
    page = resp.json()
    assert [m["body"] for m in page["items"]] == ["msg 3", "msg 4"]
    assert page["has_more"] is True
    cursor = page["next_cursor"]

    resp = client.get(f"/conversations/{conv_id}/messages?limit=2&before={cursor}", headers=auth_headers(alice))
    page = resp.json()
    assert [m["body"] for m in page["items"]] == ["msg 1", "msg 2"]
    assert page["has_more"] is True

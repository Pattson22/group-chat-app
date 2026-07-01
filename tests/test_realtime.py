from app.config import settings

from tests.helpers import auth_headers, signup


def _make_dm(client, alice, bob):
    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    assert resp.status_code == 200
    return resp.json()["id"]


def test_websocket_requires_token(client):
    with client.websocket_connect("/ws") as ws:
        # No token query param -> the server accepts (to send a real close
        # frame rather than a bare HTTP 403) then immediately closes 4401.
        data = ws.receive()
        assert data["type"] == "websocket.close"
        assert data["code"] == 4401


def test_websocket_rejects_garbage_token(client):
    with client.websocket_connect("/ws?token=not-a-real-token") as ws:
        data = ws.receive()
        assert data["type"] == "websocket.close"
        assert data["code"] == 4401


def test_send_and_receive_text_message(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    with client.websocket_connect(f"/ws?token={alice['access_token']}") as ws_alice:
        with client.websocket_connect(f"/ws?token={bob['access_token']}") as ws_bob:
            ws_alice.send_json({"conversation_id": conv_id, "text": "hello bob"})

            # broadcast_to_users fans out to every member, sender included.
            own_echo = ws_alice.receive_json()
            assert own_echo["type"] == "message"
            assert own_echo["message"]["body"] == "hello bob"

            received = ws_bob.receive_json()
            assert received["type"] == "message"
            assert received["message"]["body"] == "hello bob"
            assert received["message"]["sender_id"] == alice["user"]["id"]


def test_malformed_json_gets_system_error(client):
    alice = signup(client, display_name="Alice")
    with client.websocket_connect(f"/ws?token={alice['access_token']}") as ws:
        ws.send_text("{not valid json")
        data = ws.receive_json()
        assert data["type"] == "system"
        assert "Malformed" in data["text"]


def test_non_member_cannot_send_to_conversation(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    stranger = signup(client, display_name="Stranger")
    conv_id = _make_dm(client, alice, bob)

    with client.websocket_connect(f"/ws?token={stranger['access_token']}") as ws:
        ws.send_json({"conversation_id": conv_id, "text": "sneaky"})
        data = ws.receive_json()
        assert data["type"] == "system"
        assert "not a member" in data["text"]


def test_empty_text_and_missing_media_id_rejected(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    with client.websocket_connect(f"/ws?token={alice['access_token']}") as ws:
        ws.send_json({"conversation_id": conv_id, "text": "   "})
        data = ws.receive_json()
        assert data["type"] == "system"
        assert "must include" in data["text"]


def test_rate_limit_drops_excess_messages(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    with client.websocket_connect(f"/ws?token={alice['access_token']}") as ws:
        for i in range(settings.rate_limit_messages):
            ws.send_json({"conversation_id": conv_id, "text": f"msg {i}"})
            data = ws.receive_json()
            assert data["type"] == "message"

        # One more within the same window should be dropped with a warning
        # instead of broadcast.
        ws.send_json({"conversation_id": conv_id, "text": "one too many"})
        data = ws.receive_json()
        assert data["type"] == "system"
        assert "too fast" in data["text"]

    resp = client.get(f"/conversations/{conv_id}/messages", headers=auth_headers(bob))
    bodies = [m["body"] for m in resp.json()["items"]]
    assert "one too many" not in bodies
    assert len(bodies) == settings.rate_limit_messages

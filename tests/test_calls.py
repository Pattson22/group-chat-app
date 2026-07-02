from contextlib import ExitStack

from app.config import settings

from tests.helpers import auth_headers, signup, ws_connect


def _make_dm(client, alice, bob):
    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    assert resp.status_code == 200
    return resp.json()["id"]


def _make_group(client, owner, member_ids, name="Group"):
    resp = client.post(
        "/conversations/group", json={"name": name, "member_ids": member_ids}, headers=auth_headers(owner)
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _call_messages(client, conv_id, headers):
    resp = client.get(f"/conversations/{conv_id}/messages", headers=headers)
    assert resp.status_code == 200
    return [m for m in resp.json()["items"] if m["type"] == "call"]


def test_invite_accept_leave_logs_completed_call(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    with ws_connect(client, alice) as ws_alice:
        with ws_connect(client, bob) as ws_bob:
            ws_alice.send_json({"action": "call:invite", "conversation_id": conv_id, "video": False})

            invited = ws_alice.receive_json()
            assert invited["action"] == "call:invited"
            assert invited["rung_user_ids"] == [bob["user"]["id"]]
            call_id = invited["call_id"]

            incoming = ws_bob.receive_json()
            assert incoming["action"] == "call:incoming"
            assert incoming["call_id"] == call_id
            assert incoming["caller_id"] == alice["user"]["id"]
            assert incoming["video"] is False

            ws_bob.send_json({"action": "call:accept", "call_id": call_id})

            joined = ws_bob.receive_json()
            assert joined["action"] == "call:joined"
            assert joined["participants"] == [alice["user"]["id"]]

            participant_joined = ws_alice.receive_json()
            assert participant_joined["action"] == "call:participant-joined"
            assert participant_joined["user_id"] == bob["user"]["id"]

            ws_bob.send_json({"action": "call:leave", "call_id": call_id})
            participant_left = ws_alice.receive_json()
            assert participant_left["action"] == "call:participant-left"
            assert participant_left["user_id"] == bob["user"]["id"]

            ws_alice.send_json({"action": "call:leave", "call_id": call_id})

            # Both participants left -> the call tears down and logs a
            # "completed" message, broadcast to the whole conversation.
            logged = ws_alice.receive_json()
            assert logged["type"] == "message"
            assert logged["message"]["type"] == "call"
            assert logged["message"]["call_outcome"] == "completed"
            assert logged["message"]["call_video"] is False
            assert logged["message"]["call_duration_seconds"] is not None
            assert logged["message"]["call_duration_seconds"] >= 0

    call_msgs = _call_messages(client, conv_id, auth_headers(alice))
    assert len(call_msgs) == 1
    assert call_msgs[0]["call_outcome"] == "completed"


def test_decline_logs_missed_call(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    with ws_connect(client, alice) as ws_alice:
        with ws_connect(client, bob) as ws_bob:
            ws_alice.send_json({"action": "call:invite", "conversation_id": conv_id, "video": True})
            invited = ws_alice.receive_json()
            call_id = invited["call_id"]
            ws_bob.receive_json()  # call:incoming

            ws_bob.send_json({"action": "call:decline", "call_id": call_id})

            # The caller is also a "participant" (of one), so they get the
            # decline notice like any other participant would...
            declined = ws_alice.receive_json()
            assert declined["action"] == "call:declined"
            assert declined["user_id"] == bob["user"]["id"]

            # ...then, since nobody's left to ring and nobody else ever
            # joined, the call separately auto-ends as missed for the caller.
            ended = ws_alice.receive_json()
            assert ended["action"] == "call:ended"
            assert ended["call_id"] == call_id
            assert ended["outcome"] == "missed"

            logged = ws_alice.receive_json()
            assert logged["type"] == "message"
            assert logged["message"]["type"] == "call"
            assert logged["message"]["call_outcome"] == "missed"
            assert logged["message"]["call_video"] is True
            assert logged["message"]["call_duration_seconds"] is None

    call_msgs = _call_messages(client, conv_id, auth_headers(bob))
    assert len(call_msgs) == 1
    assert call_msgs[0]["call_outcome"] == "missed"


def test_invite_reports_unreachable_offline_member(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    # Bob never opens a websocket connection -- he's offline.
    with ws_connect(client, alice) as ws_alice:
        ws_alice.send_json({"action": "call:invite", "conversation_id": conv_id, "video": False})
        invited = ws_alice.receive_json()
        assert invited["action"] == "call:invited"
        assert invited["rung_user_ids"] == []
        assert invited["unreachable_user_ids"] == [bob["user"]["id"]]

    # Nobody was ever actually rung -- nothing worth logging.
    assert _call_messages(client, conv_id, auth_headers(alice)) == []


def test_cap_exceeded_returns_call_full(client):
    alice = signup(client, display_name="Alice")
    invitees = [signup(client, display_name=f"Member {i}") for i in range(settings.call_max_participants)]
    conv_id = _make_group(client, alice, [u["user"]["id"] for u in invitees])

    with ExitStack() as stack:
        ws_alice = stack.enter_context(ws_connect(client, alice))
        ws_invitees = [
            stack.enter_context(ws_connect(client, u)) for u in invitees
        ]

        ws_alice.send_json({"action": "call:invite", "conversation_id": conv_id, "video": False})
        invited = ws_alice.receive_json()
        call_id = invited["call_id"]
        assert len(invited["rung_user_ids"]) == settings.call_max_participants

        for ws in ws_invitees:
            ws.receive_json()  # call:incoming

        # Room starts with just the caller (1). Each successful accept
        # grows participants by one; once it reaches the cap, the next
        # accept is rejected with call_full.
        for i, ws in enumerate(ws_invitees[:-1]):
            ws.send_json({"action": "call:accept", "call_id": call_id})
            joined = ws.receive_json()
            assert joined["action"] == "call:joined", f"invitee {i} should have joined"
            ws_alice.receive_json()  # call:participant-joined broadcast to alice
            for other in ws_invitees[: i]:
                other.receive_json()  # call:participant-joined broadcast to earlier joiners

        last = ws_invitees[-1]
        last.send_json({"action": "call:accept", "call_id": call_id})
        error = last.receive_json()
        assert error["action"] == "call:error"
        assert error["reason"] == "call_full"


def test_relay_requires_participant(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    conv_id = _make_dm(client, alice, bob)

    with ws_connect(client, alice) as ws_alice:
        with ws_connect(client, bob) as ws_bob:
            ws_alice.send_json({"action": "call:invite", "conversation_id": conv_id, "video": False})
            invited = ws_alice.receive_json()
            call_id = invited["call_id"]
            ws_bob.receive_json()  # call:incoming, still ringing -- Bob has not accepted

            # Bob is only "ringing", not yet a participant -- he can't relay
            # SDP/ICE until he's actually joined.
            ws_bob.send_json(
                {
                    "action": "call:offer",
                    "call_id": call_id,
                    "target_user_id": alice["user"]["id"],
                    "sdp": {"type": "offer", "sdp": "v=0..."},
                }
            )
            error = ws_bob.receive_json()
            assert error["action"] == "call:error"
            assert error["reason"] == "not_a_participant"

from tests.helpers import auth_headers, signup


def test_create_dm_is_idempotent(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")

    resp1 = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    assert resp1.status_code == 200
    conv1 = resp1.json()
    assert conv1["type"] == "dm"
    assert {m["user_id"] for m in conv1["members"]} == {alice["user"]["id"], bob["user"]["id"]}

    resp2 = client.post("/conversations/dm", json={"other_user_id": alice["user"]["id"]}, headers=auth_headers(bob))
    assert resp2.status_code == 200
    assert resp2.json()["id"] == conv1["id"]


def test_cannot_dm_self(client):
    alice = signup(client)
    resp = client.post("/conversations/dm", json={"other_user_id": alice["user"]["id"]}, headers=auth_headers(alice))
    assert resp.status_code == 400


def test_dm_with_unknown_user_404(client):
    alice = signup(client)
    resp = client.post(
        "/conversations/dm",
        json={"other_user_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 404


def test_create_group_with_members(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    carol = signup(client, display_name="Carol")

    resp = client.post(
        "/conversations/group",
        json={"name": "Trip planning", "member_ids": [bob["user"]["id"], carol["user"]["id"]]},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 201
    conv = resp.json()
    assert conv["type"] == "group"
    assert conv["name"] == "Trip planning"
    member_ids = {m["user_id"] for m in conv["members"]}
    assert member_ids == {alice["user"]["id"], bob["user"]["id"], carol["user"]["id"]}
    alice_role = next(m["role"] for m in conv["members"] if m["user_id"] == alice["user"]["id"])
    assert alice_role == "admin"


def test_create_group_with_unknown_member_404(client):
    alice = signup(client)
    resp = client.post(
        "/conversations/group",
        json={"name": "Group", "member_ids": ["00000000-0000-0000-0000-000000000000"]},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 404


def test_list_conversations_only_shows_own(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    carol = signup(client, display_name="Carol")

    client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    client.post("/conversations/dm", json={"other_user_id": carol["user"]["id"]}, headers=auth_headers(bob))

    resp = client.get("/conversations", headers=auth_headers(alice))
    assert resp.status_code == 200
    convs = resp.json()
    assert len(convs) == 1
    assert bob["user"]["id"] in {m["user_id"] for m in convs[0]["members"]}


def test_get_conversation_404_for_non_member(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    stranger = signup(client, display_name="Stranger")

    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    conv_id = resp.json()["id"]

    resp = client.get(f"/conversations/{conv_id}", headers=auth_headers(stranger))
    assert resp.status_code == 404


def test_add_member_requires_admin(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    carol = signup(client, display_name="Carol")

    resp = client.post(
        "/conversations/group",
        json={"name": "Group", "member_ids": [bob["user"]["id"]]},
        headers=auth_headers(alice),
    )
    conv_id = resp.json()["id"]

    # Bob is a plain member, not admin -- can't add others.
    resp = client.post(
        f"/conversations/{conv_id}/members", json={"user_id": carol["user"]["id"]}, headers=auth_headers(bob)
    )
    assert resp.status_code == 403

    # Alice (admin) can.
    resp = client.post(
        f"/conversations/{conv_id}/members", json={"user_id": carol["user"]["id"]}, headers=auth_headers(alice)
    )
    assert resp.status_code == 200
    assert carol["user"]["id"] in {m["user_id"] for m in resp.json()["members"]}


def test_add_member_to_dm_rejected(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    carol = signup(client, display_name="Carol")

    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    conv_id = resp.json()["id"]

    resp = client.post(
        f"/conversations/{conv_id}/members", json={"user_id": carol["user"]["id"]}, headers=auth_headers(alice)
    )
    assert resp.status_code == 400

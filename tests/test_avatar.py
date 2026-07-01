from app.config import settings

from tests.helpers import auth_headers, signup

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844440000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "00057ffb4c2d0000000049454e44ae426082"
)


def test_upload_avatar_sets_avatar_media_id(client):
    alice = signup(client, display_name="Alice")
    assert alice["user"]["avatar_media_id"] is None

    resp = client.post(
        "/auth/me/avatar", files={"file": ("me.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    assert resp.status_code == 200
    user = resp.json()
    assert user["avatar_media_id"] is not None

    resp = client.get("/auth/me", headers=auth_headers(alice))
    assert resp.json()["avatar_media_id"] == user["avatar_media_id"]


def test_upload_avatar_rejects_non_image(client):
    alice = signup(client, display_name="Alice")
    resp = client.post(
        "/auth/me/avatar",
        files={"file": ("doc.pdf", b"%PDF-1.4 ...", "application/pdf")},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 415


def test_upload_avatar_rejects_empty_file(client):
    alice = signup(client, display_name="Alice")
    resp = client.post(
        "/auth/me/avatar", files={"file": ("me.png", b"", "image/png")}, headers=auth_headers(alice)
    )
    assert resp.status_code == 400


def test_upload_avatar_rejects_oversized_file(client):
    alice = signup(client, display_name="Alice")
    oversized = b"a" * (settings.media_max_size_bytes + 1)
    resp = client.post(
        "/auth/me/avatar", files={"file": ("me.png", oversized, "image/png")}, headers=auth_headers(alice)
    )
    assert resp.status_code == 413


def test_replacing_avatar_swaps_media_id(client):
    alice = signup(client, display_name="Alice")
    resp = client.post(
        "/auth/me/avatar", files={"file": ("first.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    first_id = resp.json()["avatar_media_id"]

    resp = client.post(
        "/auth/me/avatar", files={"file": ("second.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    second_id = resp.json()["avatar_media_id"]

    assert second_id != first_id

    # The old avatar media is orphaned but still directly fetchable by its
    # uploader (never deleted, same tradeoff as message attachments).
    resp = client.get(f"/media/{first_id}", headers=auth_headers(alice))
    assert resp.status_code == 200


def test_delete_avatar_clears_it(client):
    alice = signup(client, display_name="Alice")
    client.post("/auth/me/avatar", files={"file": ("me.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice))

    resp = client.delete("/auth/me/avatar", headers=auth_headers(alice))
    assert resp.status_code == 200
    assert resp.json()["avatar_media_id"] is None


def test_any_authenticated_user_can_fetch_someone_elses_avatar(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")

    resp = client.post(
        "/auth/me/avatar", files={"file": ("me.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    avatar_id = resp.json()["avatar_media_id"]

    # Bob has no conversation with Alice at all -- avatars are visible to
    # any authenticated user, unlike message attachments.
    resp = client.get(f"/media/{avatar_id}", headers=auth_headers(bob))
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES


def test_anonymous_user_cannot_fetch_avatar(client):
    alice = signup(client, display_name="Alice")
    resp = client.post(
        "/auth/me/avatar", files={"file": ("me.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    avatar_id = resp.json()["avatar_media_id"]

    resp = client.get(f"/media/{avatar_id}")
    assert resp.status_code == 401


def test_conversation_members_include_avatar_media_id(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    client.post(
        "/auth/me/avatar", files={"file": ("me.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )

    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    members = resp.json()["members"]
    alice_member = next(m for m in members if m["user_id"] == alice["user"]["id"])
    bob_member = next(m for m in members if m["user_id"] == bob["user"]["id"])
    assert alice_member["avatar_media_id"] is not None
    assert bob_member["avatar_media_id"] is None

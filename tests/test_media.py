from app.config import settings

from tests.helpers import auth_headers, signup

# A minimal valid 1x1 PNG.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844440000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "00057ffb4c2d0000000049454e44ae426082"
)


def test_upload_and_download_own_media(client):
    alice = signup(client, display_name="Alice")

    resp = client.post(
        "/media/upload",
        files={"file": ("pic.png", PNG_BYTES, "image/png")},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 201
    media = resp.json()
    assert media["content_type"] == "image/png"

    resp = client.get(f"/media/{media['id']}", headers=auth_headers(alice))
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES
    # Media is immutable per id, so responses must be long-cacheable and
    # must not invite content-type sniffing.
    assert "immutable" in resp.headers["cache-control"]
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_reject_unsupported_content_type(client):
    alice = signup(client, display_name="Alice")
    resp = client.post(
        "/media/upload",
        files={"file": ("script.exe", b"MZ...", "application/x-msdownload")},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 415


def test_reject_empty_file(client):
    alice = signup(client, display_name="Alice")
    resp = client.post(
        "/media/upload",
        files={"file": ("empty.png", b"", "image/png")},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 400


def test_reject_oversized_file(client):
    alice = signup(client, display_name="Alice")
    oversized = b"a" * (settings.media_max_size_bytes + 1)
    resp = client.post(
        "/media/upload",
        files={"file": ("big.txt", oversized, "text/plain")},
        headers=auth_headers(alice),
    )
    assert resp.status_code == 413


def test_uploader_can_always_fetch_own_unreferenced_upload(client):
    alice = signup(client, display_name="Alice")
    resp = client.post(
        "/media/upload", files={"file": ("pic.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    media_id = resp.json()["id"]

    # Never attached to any message/conversation -- still fetchable by the
    # uploader (e.g. to preview before sending it anywhere).
    resp = client.get(f"/media/{media_id}", headers=auth_headers(alice))
    assert resp.status_code == 200


def test_non_member_cannot_download_media_referenced_in_conversation(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")
    stranger = signup(client, display_name="Stranger")

    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    conv_id = resp.json()["id"]

    resp = client.post(
        "/media/upload", files={"file": ("pic.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    media_id = resp.json()["id"]

    # Media messages are websocket-only (see test_realtime.py for the
    # happy-path send/receive) -- reference the upload in a real message
    # here so this test can check the *access-control* boundary.
    with client.websocket_connect(f"/ws?token={alice['access_token']}") as ws_alice:
        ws_alice.send_json({"conversation_id": conv_id, "media_id": media_id})
        ws_alice.receive_json()

    resp = client.get(f"/media/{media_id}", headers=auth_headers(stranger))
    assert resp.status_code == 404


def test_member_can_download_media_after_ws_message_sent(client):
    alice = signup(client, display_name="Alice")
    bob = signup(client, display_name="Bob")

    resp = client.post("/conversations/dm", json={"other_user_id": bob["user"]["id"]}, headers=auth_headers(alice))
    conv_id = resp.json()["id"]

    resp = client.post(
        "/media/upload", files={"file": ("pic.png", PNG_BYTES, "image/png")}, headers=auth_headers(alice)
    )
    media_id = resp.json()["id"]

    with client.websocket_connect(f"/ws?token={alice['access_token']}") as ws_alice:
        with client.websocket_connect(f"/ws?token={bob['access_token']}") as ws_bob:
            ws_alice.send_json({"conversation_id": conv_id, "media_id": media_id})
            ws_alice.receive_json()
            ws_bob.receive_json()

    resp = client.get(f"/media/{media_id}", headers=auth_headers(bob))
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES

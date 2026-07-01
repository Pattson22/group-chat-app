from app.config import settings

from tests.helpers import auth_headers, signup, unique_phone


def test_request_otp_returns_dev_code(client):
    resp = client.post("/auth/request-otp", json={"phone_number": unique_phone()})
    assert resp.status_code == 200
    assert resp.json()["dev_otp_code"] is not None


def test_request_otp_rejects_malformed_phone(client):
    resp = client.post("/auth/request-otp", json={"phone_number": "not-a-phone"})
    assert resp.status_code == 422


def test_verify_otp_wrong_code_fails(client):
    phone = unique_phone()
    client.post("/auth/request-otp", json={"phone_number": phone})
    resp = client.post("/auth/verify-otp", json={"phone_number": phone, "code": "000000"})
    assert resp.status_code == 400


def test_verify_otp_success_creates_user_and_tokens(client):
    phone = unique_phone()
    resp = client.post("/auth/request-otp", json={"phone_number": phone})
    code = resp.json()["dev_otp_code"]

    resp = client.post("/auth/verify-otp", json={"phone_number": phone, "code": code})
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["user"]["phone_number"] == phone
    assert data["user"]["display_name"] is None


def test_verify_otp_reuses_existing_user_on_second_login(client):
    phone = unique_phone()
    tokens1 = signup(client, phone=phone)

    resp = client.post("/auth/request-otp", json={"phone_number": phone})
    code = resp.json()["dev_otp_code"]
    resp = client.post("/auth/verify-otp", json={"phone_number": phone, "code": code})
    tokens2 = resp.json()

    assert tokens2["user"]["id"] == tokens1["user"]["id"]
    assert tokens2["access_token"] != tokens1["access_token"]


def test_otp_request_rate_limit_per_phone(client):
    phone = unique_phone()
    for _ in range(settings.otp_request_limit_per_phone):
        resp = client.post("/auth/request-otp", json={"phone_number": phone})
        assert resp.status_code == 200

    resp = client.post("/auth/request-otp", json={"phone_number": phone})
    assert resp.status_code == 429


def test_otp_verify_lockout_after_failed_attempts(client):
    phone = unique_phone()
    client.post("/auth/request-otp", json={"phone_number": phone})

    for _ in range(settings.otp_verify_attempt_limit_per_phone):
        resp = client.post("/auth/verify-otp", json={"phone_number": phone, "code": "000000"})
        assert resp.status_code == 400

    resp = client.post("/auth/verify-otp", json={"phone_number": phone, "code": "000000"})
    assert resp.status_code == 429


def test_refresh_rotates_tokens_and_invalidates_old(client):
    tokens = signup(client)

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    rotated = resp.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]

    # The old refresh token was revoked by rotation -- reusing it must fail.
    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_logout_revokes_refresh_token(client):
    tokens = signup(client)

    resp = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 204

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_me_requires_bearer_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_rejects_garbage_token(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_update_display_name(client):
    tokens = signup(client, display_name=None)
    resp = client.patch("/auth/me", json={"display_name": "Alice"}, headers=auth_headers(tokens))
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Alice"


def test_update_display_name_rejects_blank(client):
    tokens = signup(client, display_name=None)
    resp = client.patch("/auth/me", json={"display_name": "   "}, headers=auth_headers(tokens))
    assert resp.status_code == 400

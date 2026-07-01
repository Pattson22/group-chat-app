import itertools

_phone_counter = itertools.count(1)


def unique_phone() -> str:
    return f"+1555{next(_phone_counter):07d}"


def auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def signup(client, phone: str | None = None, display_name: str | None = "Test User") -> dict:
    """Full phone+OTP signup flow against the dev OTP provider, optionally
    setting a display name. Returns the token/user payload plus the phone
    used, with `user` refreshed after the display-name PATCH if one was set.
    """
    phone = phone or unique_phone()
    resp = client.post("/auth/request-otp", json={"phone_number": phone})
    assert resp.status_code == 200, resp.text
    code = resp.json()["dev_otp_code"]

    resp = client.post("/auth/verify-otp", json={"phone_number": phone, "code": code})
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    tokens["phone_number"] = phone

    if display_name:
        resp = client.patch("/auth/me", json={"display_name": display_name}, headers=auth_headers(tokens))
        assert resp.status_code == 200, resp.text
        tokens["user"] = resp.json()

    return tokens

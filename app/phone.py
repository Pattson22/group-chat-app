import phonenumbers


def normalize_phone_number(raw: str) -> str | None:
    """Parses a phone number, returning its canonical E.164 form (e.g.
    "+15551234567") or None if it's malformed.

    Requires a leading "+" with country code -- we don't guess a default
    region, since a missing "+" almost always means malformed client input
    rather than a number we should silently assume a region for.

    Uses is_possible_number (structural plausibility: length, digit
    pattern) rather than is_valid_number (real, currently-assigned number).
    The stricter check would reject legitimate edge cases we can't
    distinguish from this input alone; actual deliverability gets
    confirmed for real when the OTP is sent.
    """
    if not raw.startswith("+"):
        return None
    try:
        parsed = phonenumbers.parse(raw, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger("app.auth.otp")


class OtpProvider(ABC):
    @abstractmethod
    async def start_verification(self, phone_number: str) -> str | None:
        """Send (or simulate) an OTP code to phone_number.

        Returns the raw code only for providers that hand code-checking
        back to us (e.g. the dev stub); real providers like Twilio Verify
        own the code lifecycle themselves and return None.
        """
        ...

    @abstractmethod
    async def check_verification(self, phone_number: str, code: str) -> bool: ...


@dataclass
class _PendingCode:
    code: str
    expires_at: float


class DevOtpProvider(OtpProvider):
    """Generates and locally verifies OTP codes instead of sending real SMS.

    State is in-memory and per-process -- fine for local/dev use, not for
    production (use TwilioOtpProvider there).
    """

    def __init__(self):
        self._pending: dict[str, _PendingCode] = {}

    async def start_verification(self, phone_number: str) -> str | None:
        code = f"{random.randint(0, 999999):06d}"
        self._pending[phone_number] = _PendingCode(
            code=code, expires_at=time.monotonic() + settings.otp_ttl_seconds
        )
        logger.warning("DEV OTP for %s: %s (expires in %ss)", phone_number, code, settings.otp_ttl_seconds)
        return code

    async def check_verification(self, phone_number: str, code: str) -> bool:
        pending = self._pending.get(phone_number)
        if pending is None:
            return False
        if time.monotonic() > pending.expires_at:
            del self._pending[phone_number]
            return False
        if pending.code != code:
            return False
        del self._pending[phone_number]
        return True


class TwilioOtpProvider(OtpProvider):
    """Sends/verifies OTP codes via Twilio Verify. Twilio owns code
    generation, storage, and expiry -- we never see or store the code."""

    def __init__(self):
        if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_verify_service_sid):
            raise RuntimeError(
                "OTP_PROVIDER=twilio requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                "and TWILIO_VERIFY_SERVICE_SID to be set"
            )
        from twilio.rest import Client

        self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        self._service_sid = settings.twilio_verify_service_sid

    async def start_verification(self, phone_number: str) -> str | None:
        import anyio

        await anyio.to_thread.run_sync(
            lambda: self._client.verify.v2.services(self._service_sid)
            .verifications.create(to=phone_number, channel="sms")
        )
        return None

    async def check_verification(self, phone_number: str, code: str) -> bool:
        import anyio

        result = await anyio.to_thread.run_sync(
            lambda: self._client.verify.v2.services(self._service_sid)
            .verification_checks.create(to=phone_number, code=code)
        )
        return result.status == "approved"


def get_otp_provider() -> OtpProvider:
    if settings.otp_provider == "twilio":
        return TwilioOtpProvider()
    return DevOtpProvider()


otp_provider = get_otp_provider()

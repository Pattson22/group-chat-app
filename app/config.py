from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/group_chat"
    rate_limit_messages: int = 5
    rate_limit_window_seconds: float = 3.0

    # JWT
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 30
    jwt_refresh_token_ttl_days: int = 30

    # OTP
    otp_provider: str = "dev"  # "dev" | "twilio"
    otp_ttl_seconds: int = 300
    otp_request_limit_per_phone: int = 3
    otp_request_limit_per_ip: int = 10
    otp_request_window_seconds: int = 600
    otp_verify_attempt_limit_per_phone: int = 5
    otp_verify_attempt_window_seconds: int = 600

    # Messages
    message_max_length: int = 4000

    # Twilio Verify (only required when otp_provider == "twilio")
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_verify_service_sid: str | None = None

    # Media storage
    storage_backend: str = "local"  # "local" | "s3"
    media_upload_dir: str = "media_uploads"
    media_max_size_bytes: int = 10 * 1024 * 1024  # 10 MB

    # S3 storage (only required when storage_backend == "s3")
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None  # set for S3-compatible providers (R2, B2, MinIO)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

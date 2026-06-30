import uuid
from abc import ABC, abstractmethod
from pathlib import Path

import anyio

from app.config import settings


class Storage(ABC):
    @abstractmethod
    async def save(self, data: bytes, content_type: str) -> str:
        """Persists data, returns a storage_key that can later retrieve it."""
        ...

    @abstractmethod
    async def read(self, storage_key: str) -> bytes:
        ...


class LocalDiskStorage(Storage):
    """Writes uploads under a gitignored local directory. Fine for
    single-instance local/dev use; not for production (use S3Storage)."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, data: bytes, content_type: str) -> str:
        key = uuid.uuid4().hex
        path = self.base_dir / key
        await anyio.to_thread.run_sync(path.write_bytes, data)
        return key

    async def read(self, storage_key: str) -> bytes:
        path = self.base_dir / storage_key
        if not path.is_file():
            raise FileNotFoundError(storage_key)
        return await anyio.to_thread.run_sync(path.read_bytes)


class S3Storage(Storage):
    """Sends/reads media via an S3-compatible bucket (AWS S3, Cloudflare R2,
    Backblaze B2, MinIO...). Requires `pip install boto3` -- not installed
    by default since storage_backend defaults to "local"."""

    def __init__(self):
        if not (settings.s3_bucket and settings.s3_region):
            raise RuntimeError("STORAGE_BACKEND=s3 requires S3_BUCKET and S3_REGION to be configured")
        import boto3

        self._client = boto3.client("s3", region_name=settings.s3_region, endpoint_url=settings.s3_endpoint_url)
        self._bucket = settings.s3_bucket

    async def save(self, data: bytes, content_type: str) -> str:
        key = uuid.uuid4().hex
        await anyio.to_thread.run_sync(
            lambda: self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        )
        return key

    async def read(self, storage_key: str) -> bytes:
        def _get() -> bytes:
            obj = self._client.get_object(Bucket=self._bucket, Key=storage_key)
            return obj["Body"].read()

        return await anyio.to_thread.run_sync(_get)


def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalDiskStorage(Path(settings.media_upload_dir))


storage = get_storage()

"""Private object storage abstraction. Local filesystem in dev, S3-compatible in production.

Clients never get permanent public URLs: uploads and downloads go through short-lived signed URLs.
"""
import io
from pathlib import Path

from django.conf import settings
from django.core import signing

UPLOAD_SALT = "wodin.storage.upload"
DOWNLOAD_SALT = "wodin.storage.download"


class StorageError(Exception):
    pass


class Storage:
    def upload_url(self, key: str, content_type: str, ttl: int) -> dict: raise NotImplementedError
    def download_url(self, key: str, ttl: int) -> str: raise NotImplementedError
    def save(self, key: str, data: bytes, content_type: str = "") -> None: raise NotImplementedError
    def read(self, key: str) -> bytes: raise NotImplementedError
    def exists(self, key: str) -> bool: raise NotImplementedError
    def size(self, key: str) -> int: raise NotImplementedError
    def delete(self, key: str) -> None: raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self):
        self.root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("Invalid storage key")
        return path

    def upload_url(self, key, content_type, ttl):
        token = signing.dumps({"k": key, "ct": content_type}, salt=UPLOAD_SALT)
        return {"url": f"{settings.BACKEND_URL}/api/storage/upload/{token}/", "method": "PUT",
                "headers": {"Content-Type": content_type}, "expires_in": ttl}

    def download_url(self, key, ttl):
        return f"{settings.BACKEND_URL}/api/storage/download/{signing.dumps({'k': key}, salt=DOWNLOAD_SALT)}/"

    def save(self, key, data, content_type=""):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, key):
        return self._path(key).read_bytes()

    def exists(self, key):
        return self._path(key).is_file()

    def size(self, key):
        return self._path(key).stat().st_size

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)

    def path_for(self, key) -> Path:
        return self._path(key)


class S3Storage(Storage):
    def __init__(self):
        import boto3

        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3", endpoint_url=settings.S3_ENDPOINT_URL or None,
            aws_access_key_id=settings.S3_ACCESS_KEY, aws_secret_access_key=settings.S3_SECRET_KEY)

    def upload_url(self, key, content_type, ttl):
        url = self.client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type}, ExpiresIn=ttl)
        return {"url": url, "method": "PUT", "headers": {"Content-Type": content_type}, "expires_in": ttl}

    def download_url(self, key, ttl):
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl)

    def save(self, key, data, content_type=""):
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_fileobj(io.BytesIO(data), self.bucket, key, ExtraArgs=extra)

    def read(self, key):
        buf = io.BytesIO()
        self.client.download_fileobj(self.bucket, key, buf)
        return buf.getvalue()

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def size(self, key):
        return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> Storage:
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage()
    if settings.STORAGE_BACKEND == "local":
        return LocalStorage()
    raise StorageError(f"Unknown STORAGE_BACKEND {settings.STORAGE_BACKEND!r}")

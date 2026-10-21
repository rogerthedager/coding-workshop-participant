"""Private attachment storage behind presigned URLs (design section 9).

The browser uploads directly with a presigned POST whose policy fixes the key and
content type and caps the size; the API then checks the object's first bytes.
"""

from typing import Protocol

from facility.config import settings
from facility.enums import MAX_ATTACHMENT_BYTES, AttachmentType
from facility.errors import fail

URL_SECONDS = 300


class Storage(Protocol):
    """Operations the attachment flow needs from object storage."""

    def presign_post(self, key: str, content_type: str) -> dict: ...

    def size(self, key: str) -> int | None: ...

    def head_bytes(self, key: str, count: int) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def presign_get(self, key: str) -> str: ...


class S3Storage:
    """boto3-backed storage for the configured private bucket."""

    def __init__(self, bucket: str, endpoint_url: str = "") -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None)

    def presign_post(self, key: str, content_type: str) -> dict:
        return self.client.generate_presigned_post(
            self.bucket,
            key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, MAX_ATTACHMENT_BYTES],
            ],
            ExpiresIn=URL_SECONDS,
        )

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError:
            return None

    def head_bytes(self, key: str, count: int) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes=0-{count - 1}")
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presign_get(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=URL_SECONDS,
        )


_override: Storage | None = None


def set_storage(storage: Storage | None) -> None:
    """Replace the storage backend (tests use an in-memory fake)."""
    global _override
    _override = storage


def get_storage() -> Storage:
    """Return the configured backend, or 503 when attachments are not configured."""
    if _override is not None:
        return _override
    if not settings.attachments_bucket:
        raise fail(503, "service_unavailable", "Attachments are not configured")
    return S3Storage(settings.attachments_bucket, settings.attachments_endpoint_url)


def signature_matches(content_type: AttachmentType, head: bytes) -> bool:
    """Check the file's magic bytes against its declared image type."""
    if content_type == AttachmentType.JPEG:
        return head.startswith(b"\xff\xd8\xff")
    if content_type == AttachmentType.PNG:
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == AttachmentType.WEBP:
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if content_type == AttachmentType.HEIC:
        return head[4:8] == b"ftyp" and head[8:12] in {b"heic", b"heix", b"mif1", b"msf1"}
    return False

"""S3-compatible object-storage backend.

Uses boto3 (lazy import — only loaded when an S3 backend is constructed).
Endpoint URL is configurable via CRM_S3_ENDPOINT so the same backend
works against AWS S3, Backblaze B2, Hetzner Object Storage, MinIO, etc.

Conditional writes via the HTTP If-Match / If-None-Match preconditions:

  - load() captures the object's ETag.
  - save() sends If-Match: <etag> to ensure nothing else wrote in the
    meantime. On rejection (HTTP 412), the backend raises
    ConcurrentWriteError so the caller can reload and retry rather
    than silently overwriting another device's changes.
  - On first run (no existing object), save() sends If-None-Match: *
    so concurrent first-creates from a second device also produce a
    conflict instead of a last-writer-wins race.

Bucket setup is the user's responsibility. Recommended:
  - private bucket (no public-read ACL)
  - server-side encryption enabled (SSE-S3 or KMS)
  - versioning enabled — recovers from accidental deletes and bad
    writes, and gives you a free undo history
"""

import json

from .errors import ConcurrentWriteError, StorageCorrupt


class S3Backend:
    def __init__(self, bucket, key, endpoint_url=None):
        try:
            import boto3
            from botocore.exceptions import ClientError
        except ImportError as e:
            raise ImportError(
                "boto3 is required for the S3 storage backend. "
                "Install with: pip install boto3"
            ) from e
        if not bucket:
            raise ValueError(
                "S3 backend requires a bucket name. Use CRM_STORAGE=s3://BUCKET/key.json"
            )
        if not key:
            raise ValueError(
                "S3 backend requires an object key. Use CRM_STORAGE=s3://bucket/KEY"
            )
        self.bucket = bucket
        self.key = key
        self._etag = None
        self._ClientError = ClientError
        self._client = boto3.client("s3", endpoint_url=endpoint_url)

    def describe(self):
        return f"s3://{self.bucket}/{self.key}"

    def load(self):
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=self.key)
        except self._ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("NoSuchKey", "404") or status == 404:
                self._etag = None
                return {"contacts": []}
            raise
        self._etag = resp.get("ETag")
        body = resp["Body"].read()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            raise StorageCorrupt(f"s3://{self.bucket}/{self.key}: {e}") from e
        if not isinstance(data, dict):
            return {"contacts": []}
        return data

    def save(self, data):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        kwargs = {
            "Bucket": self.bucket,
            "Key": self.key,
            "Body": body,
            "ContentType": "application/json",
        }
        # If we loaded an existing object, require the ETag is still current.
        # If we haven't (or the object didn't exist), require no object exists yet.
        if self._etag:
            kwargs["IfMatch"] = self._etag
        else:
            kwargs["IfNoneMatch"] = "*"
        try:
            resp = self._client.put_object(**kwargs)
        except self._ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code == "PreconditionFailed" or status == 412:
                raise ConcurrentWriteError(
                    f"data changed remotely at s3://{self.bucket}/{self.key}; "
                    "reload and retry"
                ) from e
            raise
        self._etag = resp.get("ETag")

"""S3-compatible object-storage backend.

Hand-rolled HTTP client (requests + requests-aws4auth) instead of boto3:
boto3's import alone is ~200-400 ms, which dominates startup for every
S3-touching command. requests imports in ~50 ms; requests-aws4auth is
~3 ms. We only need GET + conditional PUT, so the full AWS SDK is
overkill for this codebase.

Endpoint URL is configurable via CRM_S3_ENDPOINT so the same backend
works against AWS S3, Backblaze B2, Hetzner, MinIO, RustFS, etc.

Conditional writes via the HTTP If-Match / If-None-Match preconditions:

  - load() captures the object's ETag from the GET response.
  - save() sends If-Match: <etag> to ensure nothing else wrote in the
    meantime. On 412 PreconditionFailed, raises ConcurrentWriteError
    so the caller can reload and retry rather than silently
    overwriting another device's changes.
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
import os
from configparser import ConfigParser
from pathlib import Path
from urllib.parse import quote

from .errors import ConcurrentWriteError, StorageCorrupt


def _load_credentials():
    """Resolve AWS-style credentials. Env vars first, then ~/.aws/credentials —
    same order as boto3's default chain for the bits we care about."""
    ak = os.environ.get("AWS_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if ak and sk:
        return ak, sk

    path = Path(
        os.environ.get("AWS_SHARED_CREDENTIALS_FILE")
        or Path.home() / ".aws" / "credentials"
    )
    if not path.exists():
        raise RuntimeError(
            f"No AWS credentials in env, and {path} doesn't exist. "
            "Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY or create "
            "the credentials file."
        )
    cfg = ConfigParser()
    cfg.read(path)
    profile = os.environ.get("AWS_PROFILE", "default")
    if not cfg.has_section(profile):
        raise RuntimeError(f"No [{profile}] section in {path}.")
    try:
        return cfg[profile]["aws_access_key_id"], cfg[profile]["aws_secret_access_key"]
    except KeyError as e:
        raise RuntimeError(f"Missing {e} in [{profile}] of {path}.") from e


class S3Backend:
    def __init__(self, bucket, key, endpoint_url=None):
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
        self.endpoint_url = (endpoint_url or "https://s3.amazonaws.com").rstrip("/")
        self._etag = None
        self._session = None
        self._auth = None

    def describe(self):
        return f"s3://{self.bucket}/{self.key}"

    def _connect(self):
        """Lazy: build the requests.Session + SigV4 signer on first call."""
        if self._session is None:
            try:
                import requests
                from requests_aws4auth import AWS4Auth
            except ImportError as e:
                raise ImportError(
                    "requests + requests-aws4auth are required for the S3 "
                    "backend. Install: pip install requests requests-aws4auth"
                ) from e
            access_key, secret_key = _load_credentials()
            region = (
                os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
                or "us-east-1"
            )
            self._auth = AWS4Auth(access_key, secret_key, region, "s3")
            self._session = requests.Session()
        return self._session, self._auth

    def _url(self):
        # Path-style URL: <endpoint>/<bucket>/<key>. Most self-hosted S3
        # servers require path-style; AWS supports both.
        return f"{self.endpoint_url}/{self.bucket}/{quote(self.key, safe='/')}"

    def load(self):
        session, auth = self._connect()
        resp = session.get(self._url(), auth=auth)
        if resp.status_code == 404:
            self._etag = None
            return {"contacts": []}
        resp.raise_for_status()
        self._etag = resp.headers.get("ETag")
        try:
            data = json.loads(resp.content)
        except (json.JSONDecodeError, ValueError) as e:
            raise StorageCorrupt(f"s3://{self.bucket}/{self.key}: {e}") from e
        if not isinstance(data, dict):
            return {"contacts": []}
        return data

    def save(self, data):
        session, auth = self._connect()
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        # If we loaded an existing object, require the ETag is still current.
        # If we haven't (or the object didn't exist), require no object exists yet.
        if self._etag:
            headers["If-Match"] = self._etag
        else:
            headers["If-None-Match"] = "*"
        resp = session.put(self._url(), data=body, auth=auth, headers=headers)
        if resp.status_code == 412:
            raise ConcurrentWriteError(
                f"data changed remotely at s3://{self.bucket}/{self.key}; "
                "reload and retry"
            )
        resp.raise_for_status()
        self._etag = resp.headers.get("ETag")

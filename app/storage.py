"""DigitalOcean Spaces (S3-compatible) storage.

Files are public-read and served via the CDN. Keys include the photo id and
size, so they never change and can be cached forever.
"""
from urllib.parse import quote

import boto3
from flask import current_app

CACHE_CONTROL = "public, max-age=31536000, immutable"


class StorageNotConfigured(RuntimeError):
    pass


class Storage:
    def __init__(self, client, bucket, public_base_url, prefix=""):
        self.client = client
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/")
        self.prefix = prefix.strip("/")

    def _key(self, key):
        key = key.lstrip("/")
        return f"{self.prefix}/{key}" if self.prefix else key

    def upload(self, key, data, content_type, cache_control=CACHE_CONTROL):
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(key),
            Body=data,
            ContentType=content_type,
            CacheControl=cache_control,
            ACL="public-read",
        )

    def public_url(self, key):
        return f"{self.public_base_url}/{quote(self._key(key))}"

    def delete_prefix(self, prefix):
        """Delete every object under prefix. Refuses an empty prefix."""
        if not prefix.strip("/"):
            raise ValueError("refusing to delete with an empty prefix")
        full = self._key(prefix.rstrip("/") + "/")
        paginator = self.client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
                deleted += len(objs)
        return deleted


def make_client(region, key, secret, endpoint=None):
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint or f"https://{region}.digitaloceanspaces.com",
        aws_access_key_id=key,
        aws_secret_access_key=secret,
    )


def get_storage():
    """Return the app's Storage, building it from config on first use."""
    storage = current_app.extensions.get("storage")
    if storage is None:
        cfg = current_app.config
        missing = [
            k
            for k in ("SPACES_KEY", "SPACES_SECRET", "SPACES_BUCKET", "SPACES_REGION",
                      "SPACES_PUBLIC_BASE_URL")
            if not cfg.get(k)
        ]
        if missing:
            raise StorageNotConfigured(f"Missing config: {', '.join(missing)}")
        storage = Storage(
            make_client(cfg["SPACES_REGION"], cfg["SPACES_KEY"], cfg["SPACES_SECRET"]),
            cfg["SPACES_BUCKET"],
            cfg["SPACES_PUBLIC_BASE_URL"],
            cfg.get("SPACES_PREFIX", ""),
        )
        current_app.extensions["storage"] = storage
    return storage

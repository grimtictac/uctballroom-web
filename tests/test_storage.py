import pytest

from app.storage import CACHE_CONTROL, Storage, StorageNotConfigured, get_storage


class FakeClient:
    def __init__(self, keys=()):
        self.objects = {k: {} for k in keys}
        self.deleted = []

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class P:
            def paginate(self, Bucket, Prefix):
                keys = [k for k in client.objects if k.startswith(Prefix)]
                yield {"Contents": [{"Key": k} for k in keys]} if keys else {}

        return P()

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.deleted.append(o["Key"])
            self.objects.pop(o["Key"], None)


def test_upload_sets_public_and_cache_headers():
    c = FakeClient()
    Storage(c, "b", "https://cdn.example").upload("albums/x/1/800.webp", b"d", "image/webp")
    o = c.objects["albums/x/1/800.webp"]
    assert o["ACL"] == "public-read"
    assert o["CacheControl"] == CACHE_CONTROL
    assert o["ContentType"] == "image/webp"


def test_prefix_applied_to_key_and_url():
    c = FakeClient()
    s = Storage(c, "b", "https://cdn.example/", prefix="dev")
    s.upload("a/1.webp", b"d", "image/webp")
    assert "dev/a/1.webp" in c.objects
    assert s.public_url("a/1.webp") == "https://cdn.example/dev/a/1.webp"


def test_public_url_quotes_special_chars():
    assert Storage(FakeClient(), "b", "https://c").public_url("a b/é.webp") == (
        "https://c/a%20b/%C3%A9.webp"
    )


def test_delete_prefix_only_removes_that_prefix():
    c = FakeClient(["albums/x/1/a.webp", "albums/x/2/a.webp", "albums/xy/1/a.webp"])
    assert Storage(c, "b", "https://c").delete_prefix("albums/x") == 2
    assert list(c.objects) == ["albums/xy/1/a.webp"]


def test_delete_prefix_refuses_empty():
    with pytest.raises(ValueError):
        Storage(FakeClient(), "b", "https://c").delete_prefix("/")


def test_get_storage_requires_config(app):
    with app.app_context(), pytest.raises(StorageNotConfigured):
        get_storage()

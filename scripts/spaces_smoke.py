"""Upload a tiny file to the configured Space and fetch it via the public URL.

Run: uv run python scripts/spaces_smoke.py
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.storage import get_storage

KEY = "_smoke/hello.txt"

with create_app().app_context():
    s = get_storage()
    s.upload(KEY, b"hello", "text/plain")
    url = s.public_url(KEY)
    print("URL:", url)
    with urllib.request.urlopen(url) as r:
        print("status:", r.status)
        print("cache-control:", r.headers.get("Cache-Control"))
        assert r.read() == b"hello"
    print("deleted:", s.delete_prefix("_smoke"))
    print("OK")

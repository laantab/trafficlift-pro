"""Public Pinterest photo route: no open proxy, bounded and testable."""
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from backend import pin_image_api as pin


def test_allowed_host_and_private_host_rejection():
    assert pin._allowed("https://m.media-amazon.com/images/I/photo.jpg")
    assert pin._allowed("https://cdn.shopify.com/s/files/1/photo.jpg")
    assert not pin._allowed("https://127.0.0.1/image.jpg")
    assert not pin._allowed("http://cdn.shopify.com/photo.jpg")
    assert not pin._allowed("https://cdn.shopify.com.evil.test/photo.jpg")
    assert not pin._allowed("https://unknown.example/photo.jpg")
    assert not pin._allowed("https://user:password@cdn.shopify.com/image.jpg")


def test_real_photo_bytes_cached_without_refetch(monkeypatch, tmp_path):
    pin._CACHE.clear()
    pin._CALLS.clear()
    pin._CACHE_BYTES = 0
    calls = []
    monkeypatch.setattr(pin, "_validate_public_https_url", lambda url: None)
    def download(url, directory):
        calls.append(url)
        p = directory / "product.jpg"
        p.write_bytes(b"\\xff\\xd8" + b"x" * 2048)
        return p
    monkeypatch.setattr(pin, "_download_product_image", download)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(pin.router)
    client = TestClient(app)
    url = "https://m.media-amazon.com/images/I/verified.jpg"
    first = client.get("/api/v1/pin-image", params={"url": url})
    second = client.get("/api/v1/pin-image", params={"url": url})
    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"].startswith("image/jpeg")
    assert first.content == second.content
    assert len(calls) == 1


def test_proxy_errors_are_actionable_and_do_not_fetch_unsafe_url(monkeypatch):
    pin._CACHE.clear()
    pin._CALLS.clear()
    pin._CACHE_BYTES = 0
    monkeypatch.setattr(pin, "_validate_public_https_url", lambda url: None)
    def fail(*args):
        raise ValueError("upstream 502")
    monkeypatch.setattr(pin, "_download_product_image", fail)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(pin.router)
    client = TestClient(app)
    blocked = client.get("/api/v1/pin-image", params={"url": "https://127.0.0.1/private"})
    assert blocked.status_code == 422
    failed = client.get("/api/v1/pin-image", params={"url": "https://cdn.shopify.com/fail.jpg"})
    assert failed.status_code == 502
    assert "exact product photo" in failed.json()["detail"]

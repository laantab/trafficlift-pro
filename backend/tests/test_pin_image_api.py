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


def test_larger_photo_versions_preserve_retail_image_identity():
    amazon = "https://m.media-amazon.com/images/I/ABC123._AC_SL300_.jpg"
    variants = pin._larger_same_photo_urls(amazon)
    assert variants
    assert all("/images/I/ABC123" in candidate for candidate in variants)
    assert any("SL1500" in candidate for candidate in variants)
    assert pin._larger_same_photo_urls("https://unknown.example/photo.jpg") == []
    shopify = "https://cdn.shopify.com/s/files/1/abc/photo.jpg?width=300"
    shopify_versions = pin._larger_same_photo_urls(shopify)
    assert shopify_versions and "width=1600" in shopify_versions[0]
    assert "/photo.jpg" in shopify_versions[0]


def test_high_quality_pin_tries_same_image_at_larger_resolution(monkeypatch):
    from io import BytesIO
    from PIL import Image
    from fastapi import FastAPI
    pin._CACHE.clear()
    pin._CALLS.clear()
    pin._CACHE_BYTES = 0
    requested = []
    monkeypatch.setattr(pin, "_validate_public_https_url", lambda url: None)

    def image_bytes(size):
        output = BytesIO()
        Image.new("RGB", size, (220, 220, 220)).save(output, format="JPEG")
        return output.getvalue()

    def download(url, directory):
        requested.append(url)
        target = directory / "product.jpg"
        target.write_bytes(image_bytes((1200, 1200) if "SL1500" in url else (300, 300)))
        return target

    monkeypatch.setattr(pin, "_download_product_image", download)
    app = FastAPI()
    app.include_router(pin.router)
    client = TestClient(app)
    source = "https://m.media-amazon.com/images/I/verified._AC_SL300_.jpg"
    reply = client.get("/api/v1/pin-image", params={"url": source, "quality": "high"})
    assert reply.status_code == 200
    assert "SL1500" in requested[0]
    with Image.open(BytesIO(reply.content)) as img:
        assert img.width == 1200


def test_tiny_exact_photo_still_rejected_without_substitution(monkeypatch):
    from io import BytesIO
    from PIL import Image
    from fastapi import FastAPI
    pin._CACHE.clear()
    pin._CALLS.clear()
    pin._CACHE_BYTES = 0
    monkeypatch.setattr(pin, "_validate_public_https_url", lambda url: None)

    def download(url, directory):
        image = directory / "product.jpg"
        output = BytesIO()
        Image.new("RGB", (250, 250), (220, 220, 220)).save(output, format="JPEG")
        image.write_bytes(output.getvalue())
        return image

    monkeypatch.setattr(pin, "_download_product_image", download)
    app = FastAPI()
    app.include_router(pin.router)
    client = TestClient(app)
    reply = client.get("/api/v1/pin-image", params={
        "url": "https://m.media-amazon.com/images/I/low._AC_SL200_.jpg",
        "quality": "high",
    })
    assert reply.status_code == 422
    assert "conservative enhancement" in reply.json()["detail"]


def test_high_quality_pin_enhances_usable_same_photo_automatically(monkeypatch):
    from io import BytesIO
    from PIL import Image, ImageDraw
    from fastapi import FastAPI

    pin._CACHE.clear()
    pin._CALLS.clear()
    pin._CACHE_BYTES = 0
    monkeypatch.setattr(pin, "_validate_public_https_url", lambda url: None)

    # Mid-resolution exact source image: too small for canvas, still has detail.
    source = Image.new("RGB", (520, 520), "white")
    painter = ImageDraw.Draw(source)
    painter.rectangle((80, 80, 440, 440), fill=(45, 80, 135))
    painter.ellipse((145, 145, 375, 375), fill=(235, 170, 40))
    output = BytesIO()
    source.save(output, "JPEG")
    original_bytes = output.getvalue()
    calls = []

    def download(url, directory):
        calls.append(url)
        target = directory / "product.jpg"
        target.write_bytes(original_bytes)
        return target

    monkeypatch.setattr(pin, "_download_product_image", download)
    app = FastAPI()
    app.include_router(pin.router)
    client = TestClient(app)
    url = "https://m.media-amazon.com/images/I/actual-photo._AC_SL300_.jpg"
    result = client.get("/api/v1/pin-image", params={"url": url, "quality": "high"})
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("image/jpeg")
    assert len(calls) >= 2  # larger same-image URL(s) before enhancement
    assert all("/images/I/actual-photo" in item for item in calls)
    with Image.open(BytesIO(result.content)) as enhanced:
        assert enhanced.width >= 684
        assert enhanced.height >= 743
    assert result.content != original_bytes


def test_nearly_blank_photo_cannot_be_falsely_enhanced():
    from io import BytesIO
    from PIL import Image
    original = Image.new("RGB", (600, 600), (220, 220, 220))
    output = BytesIO()
    original.save(output, format="JPEG")
    assert pin._enhance_verified_photo(output.getvalue()) is None


def test_high_resolution_photo_remains_original_bytes(monkeypatch):
    from io import BytesIO
    from PIL import Image
    from fastapi import FastAPI
    pin._CACHE.clear()
    pin._CALLS.clear()
    pin._CACHE_BYTES = 0
    monkeypatch.setattr(pin, "_validate_public_https_url", lambda url: None)
    raw = BytesIO()
    Image.new("RGB", (1400, 1400), (100, 140, 190)).save(raw, format="JPEG")
    original = raw.getvalue()

    def download(url, directory):
        target = directory / "photo.jpg"
        target.write_bytes(original)
        return target

    monkeypatch.setattr(pin, "_download_product_image", download)
    app = FastAPI()
    app.include_router(pin.router)
    client = TestClient(app)
    result = client.get("/api/v1/pin-image", params={
        "url": "https://m.media-amazon.com/images/I/original._AC_SL1500_.jpg",
        "quality": "high",
    })
    assert result.status_code == 200
    assert result.content == original


def test_standard_mode_does_not_run_enhancement(monkeypatch):
    from io import BytesIO
    from PIL import Image
    from fastapi import FastAPI
    pin._CACHE.clear()
    pin._CALLS.clear()
    pin._CACHE_BYTES = 0
    monkeypatch.setattr(pin, "_validate_public_https_url", lambda url: None)
    raw = BytesIO()
    Image.new("RGB", (520, 520), "white").save(raw, format="JPEG")
    original = raw.getvalue()
    def download(url, directory):
        target = directory / "product.jpg"
        target.write_bytes(original)
        return target
    monkeypatch.setattr(pin, "_download_product_image", download)
    monkeypatch.setattr(pin, "_enhance_verified_photo", lambda data: (_ for _ in ()).throw(AssertionError("unexpected enhancement")))
    app = FastAPI()
    app.include_router(pin.router)
    response = TestClient(app).get("/api/v1/pin-image", params={"url": "https://m.media-amazon.com/images/I/photo.jpg"})
    assert response.status_code == 200
    assert response.content == original

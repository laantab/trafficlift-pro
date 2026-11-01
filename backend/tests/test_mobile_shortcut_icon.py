"""Factory-brand phone shortcut assets are served by TrafficLift itself."""
from io import BytesIO
from PIL import Image
from fastapi.testclient import TestClient
from trafficlift_pro import app

client = TestClient(app)

def test_phone_install_metadata_is_valid_and_brand_is_trafficlift():
    home = client.get("/")
    assert home.status_code == 200
    assert 'rel="manifest" href="/manifest.webmanifest"' in home.text
    assert 'rel="apple-touch-icon"' in home.text
    assert 'href="/mobile-icon.png"' in home.text
    assert 'id="trafficliftWorkspace"' in home.text
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert "application/manifest+json" in manifest.headers["content-type"]
    data = manifest.json()
    assert data["name"] == "TrafficLift Pro"
    assert data["display"] == "standalone"
    assert data["start_url"] == "/"
    assert {icon["src"] for icon in data["icons"]} == {"/mobile-icon.png", "/mobile-icon-512.png"}

def test_shortcut_icons_are_real_square_pngs_from_local_assets():
    for path, expected in [("/mobile-icon.png", (192, 192)), ("/mobile-icon-512.png", (512, 512))]:
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/png")
        assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
        with Image.open(BytesIO(res.content)) as icon:
            assert icon.size == expected
            assert icon.convert("RGB").getpixel((0,0))[0] < 70

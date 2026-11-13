"""Regression: keep exact seller product identity across HeyGen scenes."""
from pathlib import Path

from backend.heygen_video_api import product_prompt


def test_mat_prompt_requires_original_reference_shape():
    prompt = product_prompt("Non-Stick Silicone Baking Mat", {"mode": "product_overview", "evidence": []})
    required = [
        "BAKING MAT DEMONSTRATION SEQUENCE",
        "The EXACT supplied product photograph is the authoritative",
        "raised-edge mat with a flat pastry sheet",
        "red handled mat",
        "tan red-bordered mat",
        "do not add or remove handles",
        "Never infer product shape from the generic product title",
        "use an accurate, steady product close-up instead",
        "one continuous visible removal shot",
        "Do NOT jump directly from food-on-mat to an empty mat",
        "FLEXIBLE MAT CONTACT LOCK",
        "No food may clip through",
        "Only after the food-removal demonstration is complete",
        "Never show food floating unsupported",
        "Speak exactly the following once",
    ]
    for phrase in required:
        assert phrase in prompt


def test_general_identity_lock_applies_to_every_product():
    prompt = product_prompt("Adjustable Drawer Organizer", {"mode": "product_overview", "evidence": []})
    assert "ONE identical physical product" in prompt
    assert "opening to demonstration to final frame" in prompt
    assert "Never switch to a lookalike" in prompt
    assert "BAKING MAT DEMONSTRATION SEQUENCE" not in prompt


def test_social_export_stays_blocked_until_identity_review():
    html = (Path(__file__).resolve().parents[2] / "index.html").read_text(encoding="utf-8-sig")
    assert "exportsButton.disabled = true;" in html
    assert "identityCheck.id = 'heygenIdentityApproved';" in html
    assert "exportsButton.disabled = !identityCheck.checked;" in html
    assert "if (!identityCheck.checked)" in html
    assert "Reject — product changes in video" in html
    assert "PRODUCT IDENTITY FAILED" in html
    assert "product-verified" in html

"""No paid generation: Video Editor-in-Chief preflight regression tests."""
from unittest.mock import Mock, patch
import pytest
from fastapi import HTTPException
from backend.video_editor_in_chief import PreflightRequest, preflight, review_preflight


BASE = dict(
    provider="heygen", product_name="ThermoFlask Travel Mug with Sip-N-Lok Lid",
    image_url="https://images.example.org/exact-thermoflask.jpg",
    image_confirmed=True, duration=15, aspect_ratio="9:16",
)


def test_editor_preflight_no_provider_calls_and_reports_manual_checks():
    with patch("backend.video_editor_in_chief.video_guard"):
        report = preflight(PreflightRequest(**BASE), Mock())
    assert report["approved_for_render"] is True
    assert report["no_provider_credits_used"] is True
    assert any("same exact product" in x.lower() for x in report["review_after_render_required"])
    assert "cannot inspect footage" in report["note"]


@pytest.mark.parametrize("field, value", [
    ("image_confirmed", False),
    ("image_url", "http://localhost/photo.jpg"),
    ("product_name", "Trending Product"),
    ("duration", 2),
    ("aspect_ratio", "any"),
    ("script", "insert product here"),
    ("script", "100% guaranteed results"),
])
def test_editor_rejects_failing_inputs_before_generation(field, value):
    request = dict(BASE, **{field: value})
    with pytest.raises(HTTPException) as caught:
        review_preflight(**request)
    assert caught.value.status_code == 422


def test_editor_auto_corrects_missing_product_identity_rule():
    report = review_preflight(**dict(BASE, provider="krea",
                                     script="Stable soft-lit product demo."))
    assert "exact reference product" in report["corrected_script"].lower()
    assert len(report["automatic_corrections"]) == 1


def test_heygen_safety_prompt_is_not_mistaken_for_placeholders():
    from backend.heygen_video_api import product_prompt
    plan={"mode":"product_overview","evidence":[]}
    prompt=product_prompt(BASE["product_name"],plan)
    assert "placeholder" in prompt
    report=review_preflight(**dict(BASE,script=prompt))
    assert report["approved_for_render"] is True


def test_heygen_preflight_is_server_enforced_not_just_a_browser_check():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    heygen=(root/"backend/heygen_video_api.py").read_text()
    krea=(root/"backend/krea_video_api.py").read_text()
    free=(root/"backend/photo_video_api.py").read_text()
    html=(root/"index.html").read_text()
    assert "review_preflight(" in heygen
    assert "review_preflight(" in krea
    assert "review_preflight(" in free
    assert "await editorInChiefPreflight(" in html
    assert html.index("await editorInChiefPreflight({\n            provider:'heygen'") < html.index("High-quality video uses paid HeyGen credits")
    assert html.index("await editorInChiefPreflight({\n            provider:'krea'") < html.index("Krea API video uses separately billed credits")


def test_owner_guard_prevents_preflight_access():
    with patch("backend.video_editor_in_chief.video_guard",
               side_effect=HTTPException(401,"Owner required")):
        with pytest.raises(HTTPException) as e:
            preflight(PreflightRequest(**BASE), Mock())
    assert e.value.status_code == 401

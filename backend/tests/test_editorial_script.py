"""Zero-credit script author/editor acceptance: exact product + evidence + CTA."""
import pytest
from fastapi import HTTPException
from backend.heygen_video_api import compose_heygen_narration, product_prompt
from backend.editorial_script import editorial_review, closing_cta


@pytest.mark.parametrize("name,headline", [
    ("ThermoFlask Travel Mug with Sip-N-Lok Lid", "Coffee break?"),
    ("Generic Silicone Baking Mat, Non Stick Baking Sheet", "Next batch?"),
    ("Smart Desk Drawer Organizer", "Less clutter?"),
    ("JAS11242 Motion Sensor Night Light", "Bright idea?"),
    ("Fiskars Classic Rotary Cutter 45mm", "Curious?"),
])
def test_script_ending_matches_actual_product_and_one_caption_cta(name, headline):
    plan={"mode":"product_overview", "evidence":[]}
    narration=compose_heygen_narration(name,plan)
    report=editorial_review(name,plan,narration)
    assert report["approved"] and report["paid_calls"] == 0
    assert headline in narration
    assert narration.endswith(closing_cta(name))
    assert narration.count("Find the exact product link") == 1
    assert len(narration.split()) <= 34


def test_editor_rejects_writing_for_different_product_and_invented_claim():
    plan={"mode":"product_overview","evidence":[]}
    mug="ThermoFlask Travel Mug with Sip-N-Lok Lid"
    wrong=compose_heygen_narration("Silicone Baking Mat Non Stick",plan)
    with pytest.raises(HTTPException) as exc:
        editorial_review(mug,plan,wrong)
    assert exc.value.status_code == 422
    correct=compose_heygen_narration(mug,plan)
    for altered in (
        correct.replace("Travel Mug", "Baking Mat"),
        correct.replace(closing_cta(mug), "Buy now and save 40% guaranteed."),
        correct.replace(closing_cta(mug), "Click the button in the video."),
        correct.replace(closing_cta(mug), "Waterproof and microwave safe. "+closing_cta(mug))
    ):
        with pytest.raises(HTTPException) as exc:
            editorial_review(mug,plan,altered)
        assert exc.value.status_code == 422


def test_editor_preserves_verified_seller_fact_without_fabricating_another():
    product="Smart Desk Lamp"
    plan={"evidence":[{"text":"Adjustable neck for positioning."}]}
    narration=compose_heygen_narration(product,plan)
    assert editorial_review(product,plan,narration)["approved"]
    invented=narration.replace(closing_cta(product),
                              "Guaranteed to eliminate eye strain. "+closing_cta(product))
    with pytest.raises(HTTPException):
        editorial_review(product,plan,invented)


def test_prompt_enforces_script_review_before_any_provider_submission():
    name="ThermoFlask Travel Mug with Sip-N-Lok Lid"
    prompt=product_prompt(name,{"mode":"product_overview","evidence":[]})
    assert "Coffee break? Find the exact product link in the post caption." in prompt
    assert "Speak exactly the following once" in prompt
    assert "Never show coffee being poured OUT OF another cup" in prompt
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/"backend/heygen_video_api.py"
    source=path.read_text()
    assert source.index("editorial_review(name, plan, narration)") < source.index("    return (\n        \"Vertical product advertisement.")
    assert source.index("original_prompt = product_prompt(") < source.index('provider_request("POST", BASE,')


def test_script_review_does_not_require_user_editing_or_credits():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[2]/"index.html").read_text()
    assert "Editor-in-Chief: inspect scenes" not in html
    assert "script editor" not in html.lower()

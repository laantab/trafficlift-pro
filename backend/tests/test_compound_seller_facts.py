"""Compound seller listings can supply two real attributes without fabricating facts."""
from unittest.mock import patch
import pytest
from backend.sales_script import split_compound_seller_fact, marketing_readiness_gate
from backend.photo_video_api import VideoRequest, prepare_video_plan

LAMP = ("Silver colored lamp with an adjustable neck, on/off switch, "
        "sturdy base, and a 6-foot long cord, measuring 11 inches wide and 14.5 inches tall")
URL = "https://www.target.com/p/classic-antique-retro-adjustable-banker-desk-light"


def test_compound_lamp_fact_yields_explicit_attributes():
    facts = split_compound_seller_fact(LAMP)
    assert facts[0] == "Silver colored lamp with an adjustable neck"
    assert "Includes on/off switch" in facts
    assert "Includes sturdy base" in facts
    assert all("guaranteed" not in x.lower() for x in facts)


@pytest.mark.parametrize("text", [
    "A sturdy and stylish lamp with premium quality",
    "Bring joy, comfort, and unmatched performance to any home",
    "Adjustable neck, on/off switch",
])
def test_ambiguous_text_not_split_into_claims(text):
    assert len(split_compound_seller_fact(text)) == 1


def _listing_facts(source_url):
    return {"benefit": LAMP, "feature_details": [],
            "evidence_source": source_url, "evidence_origin": "matched_seller_page"}


def _payload():
    return VideoRequest(
        name="Classic Antique Retro Adjustable Banker Desk Light",
        image_url="https://www.target.com/images/lamp.jpg",
        product_url=URL,
        benefit=LAMP,
    )


def test_same_listing_multiple_facts_pass_preflight_without_paid_generation():
    with patch("backend.product_facts.facts_from_sources", return_value=_listing_facts(URL)), \
         patch("backend.product_facts.resolve_video_facts", return_value=_listing_facts(URL)):
        plan, _ = prepare_video_plan(_payload(), require_renderable=True)
    assert plan["mode"] == "benefit_led"
    assert marketing_readiness_gate(plan)["distinct_product_facts"] >= 2
    assert any("on/off switch" in e["text"] for e in plan["evidence"])


def test_unrelated_listing_does_not_convert_compound_text_to_verified_facts():
    with patch("backend.product_facts.facts_from_sources", return_value=_listing_facts("https://other.example/p/lamp")), \
         patch("backend.product_facts.resolve_video_facts", return_value={}):
        with pytest.raises(ValueError, match="two distinct facts"):
            prepare_video_plan(_payload(), require_renderable=True)

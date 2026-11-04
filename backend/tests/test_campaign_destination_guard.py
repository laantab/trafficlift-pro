"""Destination-approval regression tests: no retailer research URL is silently marketed."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "index.html"


def test_publication_actions_require_explicit_destination():
    page = PAGE.read_text(encoding="utf-8")
    assert "function saveCampaignDestination()" in page
    assert "function requireCampaignDestination(winner)" in page
    assert "I am authorized to promote this link" in page
    assert "requireCampaignDestination(winner);" in page
    assert "try { requireCampaignDestination(restoreCurrentWinner()); }" in page
    assert "approvedLink = requireCampaignDestination(restoreCurrentWinner());" in page


def test_retail_research_and_marketing_link_remain_distinct():
    page = PAGE.read_text(encoding="utf-8")
    assert "Research listing (seller evidence; not your promotional link)" in page
    assert "const destUrl       = _approvedCampaignDestination(winner);" in page
    assert "'Link: ' + approvedLink" in page
    assert "Product link: ${approvedLink}" in page
    assert "Pinterest Draft — approve marketing link first" in page


def test_approved_destination_is_reset_on_product_change_or_link_edit():
    page = PAGE.read_text(encoding="utf-8")
    assert "current.url !== winner.url || current.name !== winner.name" in page
    assert "function invalidateCampaignDestination()" in page
    assert "delete winner.approved_ad_url;" in page
    assert "oninput=\"invalidateCampaignDestination()\"" in page


def test_product_url_can_be_cleared_without_losing_winner():
    page = PAGE.read_text(encoding="utf-8")
    assert 'id="clearProductUrlBtn"' in page
    assert "getElementById('clearProductUrlBtn')" in page
    assert "urlInput.value = '';" in page
    assert "if (readUrlField && urlInput)" in page
    assert "persistCurrentWinner(winner);" in page
    assert "clearSelectedProduct();" in page  # only the new analyze action resets selection


def test_analyze_submits_captured_url_then_clears_field_even_if_request_fails():
    page = PAGE.read_text(encoding="utf-8")
    section = page.split("async function analyzeProductUrl()", 1)[1]
    assert "params.set('url', url);" in section
    assert section.index("const requestUrl = apiUrl(") < section.index("if (urlInput) urlInput.value = '';") < section.index("await _runPickPipeline({")
    assert 'onclick="document.getElementById(\'productUrl\').value=\'\'' in page
    assert "document.getElementById('productUrl').value = data.input_url || '';" not in page

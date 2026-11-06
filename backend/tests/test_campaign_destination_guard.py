"""Destination-approval regression tests: no retailer research URL is silently marketed."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "index.html"


def test_publication_actions_require_explicit_destination():
    page = PAGE.read_text(encoding="utf-8")
    assert "function saveCampaignDestination()" in page
    assert "function requireCampaignDestination(winner)" in page
    assert "I am authorized to promote this link" in page
    assert "try { requireCampaignDestination(restoreCurrentWinner()); }" in page
    assert "approvedLink = requireCampaignDestination(restoreCurrentWinner());" in page
    assert page.count("requireCampaignDestination(restoreCurrentWinner())") >= 3


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
    clear_button = page.split('id="clearProductUrlBtn"', 1)[1].split("</button>", 1)[0]
    assert "document.getElementById('productUrl').value='';" in clear_button
    assert "clearSelectedProduct()" not in clear_button
    assert "Clear the pasted product link without deleting your selected product" in clear_button


def test_analyze_submits_captured_url_and_keeps_it_visible_on_failure():
    page = PAGE.read_text(encoding="utf-8")
    section = page.split("async function analyzeProductUrl()", 1)[1].split("</script>", 1)[0]
    assert "urlInput.value = url;" in section
    assert "params.set('url', url);" in section
    assert section.index("const requestUrl = apiUrl(") < section.index("await _runPickPipeline({")
    assert "Keep the submitted link visible until analysis succeeds" in section
    assert "if (urlInput) urlInput.value = '';" not in section

def test_short_amazon_share_link_reaches_exact_product_analyzer():
    page = PAGE.read_text(encoding="utf-8")
    section = page.split("async function analyzeProductUrl()", 1)[1]
    assert "const shortMatch = url.match(" in section
    assert "params.set('url', url);" in section
    assert "if (shortShare)" not in section
    assert section.index("const shortMatch = url.match(") < section.index("const requestUrl = apiUrl(")
    # The explicit attach-to-verified-product control remains available as a
    # manual fallback, but it no longer intercepts share links before analysis.
    assert "Attach Link to Verified Product" in page
    assert "if (!check?.checked)" in page
    assert "winner.image_status !== 'verified'" in page


def test_video_render_does_not_require_marketing_destination_approval():
    page = PAGE.read_text(encoding="utf-8")
    section = page.split("async function submitHeyGenVideo(statusEl, resultEl)", 1)[1]
    section = section.split("async function pollHeyGenVideo", 1)[0]
    assert "requireCampaignDestination(winner);" not in section
    assert "Choose a product with a verified seller image first." in section
    assert "approve_paid_generation:true" in section
    # Publication/copy guards remain present elsewhere.
    assert page.count("requireCampaignDestination(") >= 4

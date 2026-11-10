"""Guard the Growth Strategy dashboard against duplicated markup and broken script boundaries."""
from pathlib import Path
import re

INDEX = Path(__file__).resolve().parents[2] / "index.html"


def test_dashboard_has_one_html_document_and_no_rendered_js_tail():
    html = INDEX.read_text(encoding="utf-8")
    assert html.count("</html>") == 1
    assert html.rstrip().endswith("</html>")
    assert len(re.findall(r"<script(?: [^>]*)?>", html)) == len(re.findall(r"</script>", html))


def test_saved_campaign_budget_update_remains_complete():
    html = INDEX.read_text(encoding="utf-8")
    assert "document.getElementById('budgetValue').textContent = '$' + document.getElementById('dailyBudget').value + ' / day';" in html
    assert "const product = data.scraped_product || {};" in html
    assert "const winner = {...(data.video_facts || {})" in html

import pytest
from types import SimpleNamespace
from backend import live_research as research

@pytest.fixture(autouse=True)
def no_brave_network(monkeypatch):
    monkeypatch.setattr(research,'_query_brave',lambda *a:([],[]))

def response(body,status=200):
    return SimpleNamespace(status_code=status,content=body.encode(),text=body)

def test_ddg_202_uses_independent_rss_source(monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    monkeypatch.setattr(research.requests,'post',lambda *a,**k:response('challenge',202))
    monkeypatch.setattr(research.requests,'get',lambda *a,**k:response('<rss><channel><item><title>Orbit Desk Lamp</title><link>https://seller.example/lamp</link><description>Product reviews</description></item></channel></rss>'))
    envelope=research.research('desk lamp reviews')
    assert envelope.research_status=='partial'
    assert envelope.research_provider=='bing_rss'
    assert envelope.research_sources[0]['title']=='Orbit Desk Lamp'
    assert envelope.research_errors==['DuckDuckGo HTTP 202','Bing web returned no parsed result cards']

def test_empty_feed_does_not_create_product_and_explains_providers(monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    monkeypatch.setattr(research.requests,'post',lambda *a,**k:response('challenge',202))
    monkeypatch.setattr(research.requests,'get',lambda *a,**k:response('<rss><channel/></rss>'))
    envelope=research.research('products')
    assert envelope.research_status=='fallback' and envelope.research_sources==[]
    assert envelope.research_errors==['DuckDuckGo HTTP 202','Bing web returned no parsed result cards','Bing RSS returned no results']

def test_non_feed_and_invalid_links_are_not_research(monkeypatch):
    monkeypatch.setattr(research.requests,'get',lambda *a,**k:response('<html>Access denied</html>'))
    assert research._query_bing_rss('products',6)==([],[])
    monkeypatch.setattr(research.requests,'get',lambda *a,**k:response('<rss><channel><item><title>Fake product</title><link>javascript:bad</link></item></channel></rss>'))
    assert research._query_bing_rss('products',6)==([],[])

def test_successful_ddg_does_not_request_rss(monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    monkeypatch.setattr(research,'_query_duckduckgo',lambda *a:([research.ResearchSource('Lamp','reviews','https://seller.example/lamp','duckduckgo')],[]))
    monkeypatch.setattr(research,'_query_bing_rss',lambda *a:(_ for _ in ()).throw(AssertionError('Unnecessary fallback')))
    assert research.research('lamp').research_provider=='duckduckgo'

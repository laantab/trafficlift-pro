import json, base64
from types import SimpleNamespace
from backend import live_research, discovery, listing_photo

def response(text):
    return SimpleNamespace(status_code=200,content=text.encode(),text=text)

def test_html_results_preserve_real_listing_url(monkeypatch):
    destination='https://www.target.com/p/orbit-adjustable-desk-lamp/-/A-12345'
    encoded='a1'+base64.urlsafe_b64encode(destination.encode()).decode().rstrip('=')
    html=f'<ol><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u={encoded}">Orbit Adjustable Desk Lamp : Target</a></h2><div class="b_caption"><p>32 ratings and reviews</p></div></li></ol>'
    monkeypatch.setattr(live_research.requests,'get',lambda *a,**k:response(html))
    hits,_=live_research._query_bing_html('desk lamp site:target.com/p/',6)
    assert hits[0].url==destination
    assert hits[0].snippet=='32 ratings and reviews'
    assert live_research._filter_search_hits('desk lamp site:target.com/p/',hits)==hits

def test_fallback_does_not_accept_store_directory_or_unrelated_article():
    hits=[live_research.ResearchSource('Petco store','popular products','https://www.petco.com/','bing_rss'),
          live_research.ResearchSource('Milwaukee Pipeline 2026: Every New Tool Announced','new product reviews','https://news.example/2026/tool-announcement','bing_rss'),
          live_research.ResearchSource('Desks - Target','reviews','https://www.target.com/c/desks','bing_rss')]
    assert not live_research._filter_search_hits('desk lamp site:target.com/p/',hits)
    env=SimpleNamespace(research_sources=[dict(title=h.title,snippet=h.snippet,url=h.url) for h in hits],research_query='test')
    assert discovery._build_candidates_from_envelope(env,set())==[]

def test_reviews_only_in_listing_are_available_to_qualification(monkeypatch):
    product={'@type':'Product','name':'Orbit Desk Lamp','image':'https://cdn.example/photo.jpg',
             'aggregateRating':{'ratingValue':'4.4','reviewCount':'37','bestRating':'5'}}
    html='<script type="application/ld+json">'+json.dumps(product)+'</script>'
    monkeypatch.setattr(listing_photo,'_safe_get',lambda *a,**k:('https://seller.example/product',html,None))
    details={}
    assert listing_photo.listing_images('Orbit Desk Lamp','https://seller.example/product',details=details)
    assert details['seller_rating']=={'value':4.4,'count':37,'best':5.0}
    other={}
    listing_photo.listing_images('Other Desk Lamp','https://seller.example/product',details=other)
    assert 'seller_rating' not in other

def test_blocked_search_and_offsite_feed_are_unavailable(monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    monkeypatch.setattr(live_research,'_query_brave',lambda *a:([],[]))
    monkeypatch.setattr(live_research,'_query_duckduckgo',lambda *a:([],[]))
    monkeypatch.setattr(live_research,'_query_bing_html',lambda *a:([],[]))
    monkeypatch.setattr(live_research,'_query_bing_rss',lambda *a:([live_research.ResearchSource('Petco','reviews','https://petco.com','bing_rss')],[]))
    env=live_research.research('pet water fountain site:target.com/p/')
    assert env.research_status=='fallback'
    assert not env.research_sources


def test_brave_primary_uses_actual_titles_and_skips_blocked_fallbacks(monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    html='<div class="result-content"><a href="https://www.target.com/p/orbit-lamp/-/A-123"><div class="search-snippet-title" title="Orbit Adjustable Desk Lamp : Target">Orbit</div></a><div class="generic-snippet">Read reviews and buy the Orbit lamp.</div></div>'
    monkeypatch.setattr(live_research.requests,'get',lambda *a,**k:response(html))
    monkeypatch.setattr(live_research,'_query_duckduckgo',lambda *a:(_ for _ in ()).throw(AssertionError('No need for DDG')))
    env=live_research.research('desk lamp site:target.com/p/')
    assert env.research_provider=='brave_web'
    assert env.research_sources[0]['title']=='Orbit Adjustable Desk Lamp : Target'
    assert env.research_sources[0]['snippet']=='Read reviews and buy the Orbit lamp.'
    assert env.research_image_urls==[]


def test_long_seller_title_keeps_product_prefix_and_never_uses_snippet_fragment():
    title='Amazon.com: Simple Houseware 4-Pack Drawer Organizer Set for Underwear/Socks/Bra, Gray | Closet Dividers for Underwear, Socks, Bras, Scarves, Ties - Foldable Non-Woven Storage Boxes : Home & Kitchen'
    name=discovery._normalize_title(title)
    assert name=='Simple Houseware 4-Pack Drawer Organizer Set for Underwear/Socks/Bra, Gray'
    assert len(name)<=90
    assert discovery._normalize_title('Walmart', 'Perfect for placing next to table or bed') is None

from types import SimpleNamespace
import json
import pytest
from fastapi import HTTPException
from backend import discovery_engine as engine,live_research,listing_photo,product_control_agent,discovery
from backend.live_research import ResearchEnvelope
@pytest.fixture
def dress(monkeypatch):
 source=dict(title='Ivory Lace Wedding Dress With Pockets',url='https://seller.com/products/ivory-dress',snippet='Ivory Lace Wedding Dress With Pockets has customer reviews and includes pockets.')
 env=ResearchEnvelope(research_status='live',research_timestamp='2026-10-07T18:00:00Z',research_provider='fixture',research_sources=[source],research_image_urls=['https://seller.com/review-mug.jpg'],research_query='dress')
 monkeypatch.setattr(live_research,'research',lambda *a,**k:env)
 monkeypatch.setattr(product_control_agent,'_validate_image',lambda url,**kw:product_control_agent.ImageCheckResult(ok=True,image_status='verified',image_url=url))
 monkeypatch.setattr(discovery,'_find_image_for_candidate',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Shared research photos must not be used')))
 return source

def test_dress_does_not_use_shared_research_mug(dress,monkeypatch):
 monkeypatch.setattr(listing_photo,'listing_images',lambda *a,**kw:[])
 with pytest.raises(HTTPException) as exc:engine._discover_batch(broad_market=True,require_signals=True)
 assert exc.value.status_code==404 and 'photo rejections: 1' in exc.value.detail

def test_dress_uses_bound_listing_photo(dress,monkeypatch):
 monkeypatch.setattr(listing_photo,'listing_images',lambda *a,**kw:['https://seller.com/dress.jpg'])
 winner=engine._discover_batch(broad_market=True,require_signals=True)
 assert winner['image_url']=='https://seller.com/dress.jpg' and winner['image_origin']=='matched_product_listing'

def test_product_photo_preferred_to_marketing_open_graph(monkeypatch):
 url='https://seller.com/products/ivory-dress'
 html='<meta property="og:title" content="Ivory Lace Wedding Dress"><meta property="og:image" content="https://seller.com/review-mug.jpg"><script type="application/ld+json">'+json.dumps({'@type':'Product','name':'Ivory Lace Wedding Dress','url':url,'image':'https://seller.com/dress.jpg'})+'</script>'
 monkeypatch.setattr(listing_photo,'_safe_get',lambda *a,**k:(url,html,None))
 assert listing_photo.listing_images('Ivory Lace Wedding Dress',url)==['https://seller.com/dress.jpg']

def test_redirected_listing_cannot_supply_same_named_wrong_photo(monkeypatch):
 url='https://seller.com/products/ivory-dress'
 html='<meta property="og:title" content="Ivory Lace Wedding Dress"><meta property="og:image" content="https://seller.com/review-mug.jpg">'
 monkeypatch.setattr(listing_photo,'_safe_get',lambda *a,**k:('https://seller.com/products/other-product',html,None))
 assert listing_photo.listing_images('Ivory Lace Wedding Dress',url)==[]

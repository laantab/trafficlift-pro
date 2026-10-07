from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend import discovery_engine as engine,live_research
@pytest.fixture
def provider(monkeypatch):monkeypatch.setattr(live_research,'_is_tavily_configured',lambda:True)
def test_empty_first_batch_recovers_in_same_click(provider,monkeypatch):
 calls=[]
 def batch(**kw):
  calls.append(kw)
  if len(calls)==1:raise HTTPException(404,'Only articles returned')
  return {'name':'Verified seller product','discovery':{}}
 monkeypatch.setattr(engine,'_discover_batch',batch)
 result=engine.discover(client_id='c'*32,broad_market=True,require_signals=True)
 assert len(calls)==2 and result['discovery']['research_batches']==2
 assert all(c['require_signals'] for c in calls)
 assert calls[0]['request_deadline']<calls[1]['request_deadline']
@pytest.mark.parametrize('status',[401,402,429,503])
def test_provider_failure_is_not_retried(provider,monkeypatch,status):
 calls=[]
 def batch(**kw):calls.append(kw);raise HTTPException(status,'Provider unavailable')
 monkeypatch.setattr(engine,'_discover_batch',batch)
 with pytest.raises(HTTPException) as exc:engine.discover(broad_market=True)
 assert len(calls)==1 and exc.value.status_code==status

def test_recovery_is_bounded_and_never_invents_winner(provider,monkeypatch):
 calls=[]
 def batch(**kw):calls.append(kw);raise HTTPException(404,'No photo or buying evidence')
 monkeypatch.setattr(engine,'_discover_batch',batch)
 with pytest.raises(HTTPException) as exc:engine.discover(broad_market=True)
 assert len(calls)==2 and 'No unverified product was substituted' in exc.value.detail

def test_listing_snippet_footer_does_not_reject_real_product():
 from backend.discovery import _build_candidates_from_envelope
 env=SimpleNamespace(research_query='actual listing research',research_sources=[dict(title='Amazon.com: Acme Travel Bottle',url='https://amazon.com/dp/B012345678',snippet='Acme Travel Bottle has 1500 reviews. Includes removable lid. Privacy policy. Contact us.')])
 cards=_build_candidates_from_envelope(env,set())
 assert len(cards)==1 and cards[0].name=='Acme Travel Bottle'
 for title,url in [('21 Trending Products to Sell','https://seller.com/blog/top'),('Privacy Policy','https://seller.com/privacy')]:
  assert not _build_candidates_from_envelope(SimpleNamespace(research_sources=[dict(title=title,url=url,snippet='Travel bottle reviews')]),set())

def test_actual_listing_pipeline_qualifies_buying_signal_photo(provider,monkeypatch):
 from backend import product_control_agent,listing_photo
 from backend.live_research import ResearchEnvelope
 source=dict(title='Amazon.com: Acme Travel Bottle',url='https://amazon.com/dp/B012345678',snippet='Acme Travel Bottle has 1500 reviews; includes a removable lid. Privacy policy.')
 env=ResearchEnvelope(research_status='live',research_timestamp='2026-10-06T22:00:00Z',research_provider='fixture',research_sources=[source],research_image_urls=[],research_query='listing query')
 queries=[]
 def research(q,**kw):queries.append(q);return env
 monkeypatch.setattr(live_research,'research',research)
 monkeypatch.setattr(listing_photo,'listing_images',lambda *a,**kw:['https://seller.com/bottle.jpg'])
 monkeypatch.setattr(product_control_agent,'_validate_image',lambda url,**kw:product_control_agent.ImageCheckResult(ok=True,image_status='verified',image_url=url))
 winner=engine._discover_batch(broad_market=True,require_signals=True,seed=1)
 assert winner['name']=='Acme Travel Bottle' and winner['image_status']=='verified'
 assert winner['research_sources'] and winner['discovery']['market_signal_sources']
 assert len(queries)==3 and all('site:' in q or 'inurl:' in q for q in queries)


def test_classic_button_uses_same_recovery_engine(monkeypatch):
 from backend.discovery import discover_winner
 calls=[]
 def discover(**kw):calls.append(kw);return {'name':'Verified product'}
 monkeypatch.setattr(engine,'discover',discover)
 assert discover_winner(client_id='c'*32)['name']=='Verified product'
 assert calls[0]['broad_market'] is True and calls[0]['require_signals'] is True

def test_classic_http_endpoint_recovers_after_empty_batch(provider,monkeypatch):
 from fastapi.testclient import TestClient
 from trafficlift_pro import app
 calls=[]
 def batch(**kw):
  calls.append(kw)
  assert kw['broad_market'] and kw['require_signals']
  if len(calls)==1:raise HTTPException(404,'No concrete product')
  return {'name':'Verified product','discovery':{}}
 monkeypatch.setattr(engine,'_discover_batch',batch)
 with TestClient(app) as client:
  response=client.get('/api/v1/discover-winner?client_id='+'c'*32)
 assert response.status_code==200 and response.json()['discovery']['research_batches']==2
 assert len(calls)==2

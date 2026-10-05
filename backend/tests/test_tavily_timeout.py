from unittest.mock import patch
from types import SimpleNamespace
import requests
from backend.live_research import _query_tavily


def test_timeout_then_success_returns_live_product():
    response=SimpleNamespace(status_code=200,json=lambda:{'results':[{'title':'Orbit Desk Lamp','url':'https://seller.example/products/lamp','content':'Adjustable arm for positioning light.'}]})
    with patch('backend.live_research._is_tavily_configured',return_value=True),patch('backend.live_research.requests.post',side_effect=[requests.exceptions.ReadTimeout(),response]) as post:
        hits,_=_query_tavily('lamp',6)
    assert hits[0].title=='Orbit Desk Lamp'
    assert post.call_count==2
    assert post.call_args.kwargs['timeout']==(3,10)


def test_persistent_timeout_stops_after_one_retry():
    with patch('backend.live_research._is_tavily_configured',return_value=True),patch('backend.live_research.requests.post',side_effect=requests.exceptions.ReadTimeout()) as post:
        assert _query_tavily('lamp',6)==([],[])
    assert post.call_count==2


def test_auth_and_quota_are_not_retried():
    for status in [401,429,432,433]:
        with patch('backend.live_research._is_tavily_configured',return_value=True),patch('backend.live_research.requests.post',return_value=SimpleNamespace(status_code=status)) as post:
            assert _query_tavily('lamp',6)==([],[])
        assert post.call_count==1

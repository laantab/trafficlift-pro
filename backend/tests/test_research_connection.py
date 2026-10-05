import os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from backend import research_config as config, photo_video_api as api, discovery_engine as engine, live_research

KEY='tvly-test-local-key-123456789'


def test_project_file_loaded_with_bom_from_different_working_directory(tmp_path,monkeypatch):
    (tmp_path/'.env').write_text('TAVILY_API_KEY='+KEY+'\n',encoding='utf-8-sig')
    other=tmp_path/'other';other.mkdir();monkeypatch.chdir(other)
    monkeypatch.setenv('TRAFFICLIFT_FREE_VIDEO','1')
    monkeypatch.setenv('TAVILY_API_KEY','')
    status=config.load_research_config(tmp_path)
    assert os.environ['TAVILY_API_KEY']==KEY
    assert status['configured'] and status['source']=='project_file'
    assert KEY not in str(status)


def test_local_file_overrides_stale_inherited_key_but_production_does_not(tmp_path,monkeypatch):
    (tmp_path/'.env').write_text('TAVILY_API_KEY='+KEY)
    monkeypatch.setenv('TAVILY_API_KEY','tvly-stale-key-123456789')
    monkeypatch.delenv('TRAFFICLIFT_FREE_VIDEO',raising=False)
    config.load_research_config(tmp_path)
    assert os.environ['TAVILY_API_KEY']=='tvly-stale-key-123456789'
    monkeypatch.setenv('TRAFFICLIFT_FREE_VIDEO','1')
    config.load_research_config(tmp_path)
    assert os.environ['TAVILY_API_KEY']==KEY


def test_save_preserves_other_settings_and_updates_running_process(tmp_path,monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    (tmp_path/'.env').write_text('OPENAI_API_KEY=leave-alone\nTAVILY_API_KEY=old\nOTHER_SETTING=unchanged\n')
    status=config.save_research_key(KEY,tmp_path)
    assert status['configured']
    assert 'OPENAI_API_KEY=leave-alone' in (tmp_path/'.env').read_text()
    assert 'OTHER_SETTING=unchanged' in (tmp_path/'.env').read_text()
    assert (tmp_path/'.env').read_text().count('TAVILY_API_KEY=')==1
    assert live_research._is_tavily_configured()
    assert KEY not in str(status)
    assert not (tmp_path/'.env.research.tmp').exists()


def test_local_api_saves_key_without_provider_call(tmp_path,monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_FREE_VIDEO','1');monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    monkeypatch.setattr(config,'ROOT',tmp_path)
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as client, patch.object(live_research.requests,'post') as provider:
        result=client.post('/api/v1/research-connection',json={'api_key':KEY})
        assert result.status_code==200 and result.json()['configured']
        assert KEY not in result.text
        assert client.get('/api/v1/research-connection').json()['configured']
        assert client.post('/api/v1/research-connection',headers={'Origin':'https://foreign.example'},json={'api_key':KEY}).status_code==403
        provider.assert_not_called()


def test_missing_connection_stops_before_any_search(monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_FREE_VIDEO','1');monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    with patch.object(live_research,'research') as research:
        with pytest.raises(HTTPException) as exc:engine.discover()
    assert exc.value.status_code==503
    assert 'RESEARCH_CONNECTION_MISSING' in exc.value.detail
    research.assert_not_called()


def test_invalid_key_is_not_masked_by_public_search_fallback(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY',KEY)
    monkeypatch.setattr(live_research.requests,'post',lambda *a,**k:SimpleNamespace(status_code=401))
    with patch.object(live_research,'_query_brave') as brave, patch.object(live_research,'_query_duckduckgo') as ddg:
        result=live_research.research('drawer organizer')
    assert result.research_status=='fallback' and result.research_provider=='tavily'
    assert 'Tavily HTTP 401' in result.research_errors
    brave.assert_not_called();ddg.assert_not_called()

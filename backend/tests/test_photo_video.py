"""Free studio regressions: no paid calls, job failures, persistence, checks."""
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import photo_video_api as api
from backend.photo_video import check_video

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_FREE_VIDEO','1')
    monkeypatch.setenv('TRAFFICLIFT_VIDEO_DIR',str(tmp_path))
    monkeypatch.setattr(api,'_validate_public_https_url',lambda x: None)
    app=FastAPI();app.include_router(api.router)
    with TestClient(app) as c:yield c

@pytest.fixture
def payload():return {'name':'Lamp','image_url':'https://example.com/lamp.jpg','product_url':'https://example.com/lamp','benefit':'A warm glow for your room','seconds':30,'style':'warm'}

def completed(tmp_path,status='succeeded'):
    job='a'*32;directory=tmp_path/job;directory.mkdir()
    record={'id':job,'name':'Lamp','created_at':1,'status':status,'message':'Ready','video_url':f'/api/v1/photo-videos/{job}/video' if status=='succeeded' else None,'product_url':'https://example.com/lamp'}
    api.save(record,directory);(directory/'video.mp4').write_bytes(b'test-video');return job

def test_disabled_returns_explanation(client,monkeypatch):
    monkeypatch.delenv('TRAFFICLIFT_FREE_VIDEO');assert client.get('/api/v1/photo-videos').status_code==503

def test_remote_and_cross_origin_blocked(client):
    assert client.get('/api/v1/photo-videos',headers={'Host':'evil.example'}).status_code==403
    assert client.get('/api/v1/photo-videos',headers={'Origin':'https://evil.example'}).status_code==403

def test_setup_missing_no_job(client,payload,tmp_path):
    with patch.object(api,'dependencies',side_effect=ValueError('Setup required')):
        assert client.post('/api/v1/photo-videos',json=payload).status_code==503
    assert not list(tmp_path.iterdir())

def test_blank_benefit_starts_preview_job(client,payload,tmp_path):
    payload['benefit']=''
    payload['seconds']=15
    with patch.object(api,'dependencies'),patch.object(api.threading,'Thread') as thread:
        try:
            response=client.post('/api/v1/photo-videos',json=payload)
            assert response.status_code==202
            job=response.json()['video']
            saved=json.loads((tmp_path/job['id']/'job.json').read_text())
            assert saved['sales_plan']['mode']=='product_overview'
            assert saved['sales_plan']['review']['status']=='PASS'
            assert saved['sales_plan']['evidence']==[]
            thread.return_value.start.assert_called_once()
        finally:
            if api.lock.locked():api.lock.release()

def test_reviewed_sales_plan_saved_with_job(client,payload,tmp_path):
    payload.update(buyer_need='A warmer room',consideration='Includes a stand')
    with patch.object(api,'dependencies'),patch.object(api.threading,'Thread'):
        try:
            response=client.post('/api/v1/photo-videos',json=payload)
            assert response.status_code==202
            job=response.json()['video']
            saved=json.loads((tmp_path/job['id']/'job.json').read_text())
            assert saved['sales_plan']['review']['status']=='PASS'
            assert saved['sales_plan']['buyer_need']=='A warmer room'
            assert saved['sales_plan']['evidence'][0]['source_url']==payload['product_url']
        finally:
            if api.lock.locked():api.lock.release()

@pytest.mark.parametrize('field,value',[('seconds',16),('style','unknown'),('name',''),('benefit','x'*161),('name','{bad}'),('benefit',r'\NInjected')])
def test_bad_payload(client,payload,field,value):
    payload[field]=value;assert client.post('/api/v1/photo-videos',json=payload).status_code==422

def test_duplicate_blocked(client,payload):
    with patch.object(api,'dependencies'),patch.object(api,'lock') as lock:
        lock.acquire.return_value=False
        assert client.post('/api/v1/photo-videos',json=payload).status_code==409

def test_history_persists_and_safe_download(client,tmp_path):
    job=completed(tmp_path)
    assert client.get('/api/v1/photo-videos').json()['videos'][0]['name']=='Lamp'
    assert client.get(f'/api/v1/photo-videos/{job}/video').content==b'test-video'
    assert client.get('/api/v1/photo-videos/not-a-job/video').status_code==404

@pytest.mark.parametrize('status',['failed','running','queued'])
def test_no_download_until_checked(client,tmp_path,status):
    job=completed(tmp_path,status);assert client.get(f'/api/v1/photo-videos/{job}/video').status_code==409

def test_missing_file_cannot_download(client,tmp_path):
    job=completed(tmp_path);(tmp_path/job/'video.mp4').unlink()
    assert client.get(f'/api/v1/photo-videos/{job}/video').status_code==404

def test_worker_failure_records_and_releases_lock(client,tmp_path,payload):
    request=api.VideoRequest(**payload);job='b'*32;directory=tmp_path/job;directory.mkdir()
    record={'id':job,'status':'queued'}
    with patch.object(api,'resolve_video_facts',return_value={}), patch.object(api,'_download_product_image',side_effect=ValueError('Bad photo')),patch.object(api,'lock') as lock:
        api.worker(record,request,directory)
        assert record['status']=='failed';assert record['video_url'] is None;lock.release.assert_called_once()
        assert json.loads((directory/'job.json').read_text())['message']=='Bad photo'

def probe(width=1080,duration=15,audio=True):
    streams=[{'codec_type':'video','codec_name':'h264','width':width,'height':1920,'pix_fmt':'yuv420p'}]
    if audio:streams.append({'codec_type':'audio','codec_name':'aac'})
    return json.dumps({'streams':streams,'format':{'duration':str(duration)}}).encode()

@pytest.mark.parametrize('data',[probe(width=720),probe(duration=14),probe(audio=False)])
def test_strict_format_length_audio(data):
    with patch('backend.photo_video.subprocess.check_output',return_value=data),patch('backend.photo_video.subprocess.run') as run:
        with pytest.raises(ValueError):check_video('output.mp4',15)
        run.assert_not_called()

def test_decode_error_is_not_pass():
    import subprocess
    with patch('backend.photo_video.subprocess.check_output',return_value=probe()),patch('backend.photo_video.subprocess.run',side_effect=subprocess.CalledProcessError(1,'ffmpeg')):
        with pytest.raises(subprocess.CalledProcessError):check_video('output.mp4',15)

def test_dashboard_local_api_and_preview(client):
    html=client.get('/studio').text
    assert '<meta name="api-base" content="">' in html
    assert 'id="freeProductPreview"' in html
    assert 'id="freeVideoHistory"' in html


def test_motion_check_rejects_static_photo():
    from backend.photo_video import check_motion
    with patch('backend.photo_video.subprocess.check_output',return_value=bytes(96*96*3*15)):
        with pytest.raises(ValueError,match='staying still'):check_motion('static.mp4',15)

def test_motion_check_accepts_changing_photo():
    from backend.photo_video import check_motion
    data=b''.join(bytes([i*10])*(96*96*3) for i in range(15))
    with patch('backend.photo_video.subprocess.check_output',return_value=data):
        assert check_motion('moving.mp4',15)['status']=='PASS'


def test_store_guide_never_starts_video_worker(client,payload,tmp_path):
    payload['name']='The 10 Best Light Fixture Stores for 2026 | Free Buyers Guide'
    with patch.object(api,'dependencies') as setup,patch.object(api.threading,'Thread') as thread:
        response=client.post('/api/v1/photo-videos',json=payload)
        assert response.status_code==422
        assert 'one specific product' in response.json()['detail']
        setup.assert_not_called();thread.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_status_save_retries_windows_file_lock(tmp_path):
    original=Path.replace
    attempts=[]
    def replace(path,target):
        attempts.append(path)
        if len(attempts)<3:raise PermissionError(5,'Access is denied')
        return original(path,target)
    with patch.object(Path,'replace',replace),patch.object(api.time,'sleep'):
        api.save({'id':'a','status':'running'},tmp_path)
    assert len(attempts)==3
    assert json.loads((tmp_path/'job.json').read_text())['status']=='running'
    assert not list(tmp_path.glob('*.tmp'))


def test_old_video_history_and_download_remain_available(client,tmp_path,monkeypatch):
    legacy=tmp_path/'legacy';legacy.mkdir()
    job=completed(legacy)
    monkeypatch.setenv('TRAFFICLIFT_LEGACY_VIDEO_DIR',str(legacy))
    monkeypatch.setenv('TRAFFICLIFT_VIDEO_DIR',str(tmp_path/'new'))
    assert client.get('/api/v1/photo-videos').json()['videos'][0]['id']==job
    assert client.get(f'/api/v1/photo-videos/{job}/video').status_code==200

def test_pin_photo_same_origin_cached_png(client,tmp_path):
    def download(url,directory):
        path=directory/'product.png';path.write_bytes(b'png-fixture');return path
    with patch.object(api,'_download_product_image',side_effect=download) as fetch:
        first=client.get('/api/v1/product-image',params={'url':'https://example.com/product.png'})
        second=client.get('/api/v1/product-image',params={'url':'https://example.com/product.png'})
    assert first.status_code==second.status_code==200
    assert first.content==second.content==b'png-fixture'
    assert first.headers['content-type']=='image/png'
    fetch.assert_called_once()

def test_pin_photo_rejects_unsafe_and_remote(client):
    with patch.object(api,'_validate_public_https_url',side_effect=ValueError('private')):
        assert client.get('/api/v1/product-image',params={'url':'https://127.0.0.1/a'}).status_code==422
    assert client.get('/api/v1/product-image',params={'url':'https://example.com/a'},headers={'Origin':'https://evil.example'}).status_code==403

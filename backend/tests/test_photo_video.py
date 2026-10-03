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
def payload():return {'name':'Lamp','image_url':'https://example.com/lamp.jpg','product_url':'https://example.com/lamp','benefit':'','seconds':15,'style':'warm'}

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
    with patch.object(api,'_download_product_image',side_effect=ValueError('Bad photo')),patch.object(api,'lock') as lock:
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

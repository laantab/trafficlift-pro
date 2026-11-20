"""Offline publishing acceptance: no credentials, generation or live uploads."""
import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import publishing_api as api
from backend import publishing_store as store
from backend import youtube_publisher as youtube
INSPECT_MEDIA = api.inspect_media

HOST = 'https://trafficlift.test'
SECRET = 'test-owner-secret-' + 'x' * 40
CHANNEL = 'UC' + 'a' * 22
VIDEO = 'abc12345678'
SESSION = 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test-session'
LINK = 'https://www.amazon.com/dp/B0HQ84DS7S?tag=owner'
PRODUCT = {'id': 'campaign-one', 'title': 'A Season for Everyone',
           'product_url': 'https://www.amazon.com/dp/B0HQ84DS7S'}
TECHNICAL = {'width': 720, 'height': 1280, 'duration': 15.0, 'audio_codec': 'aac', 'video_codec': 'h264'}


def response(status=200, value=None, headers=None):
    return Mock(status_code=status, json=Mock(return_value=value or {}), headers=headers or {})


@pytest.fixture
def setup(monkeypatch, tmp_path):
    for name, value in {'TRAFFICLIFT_HOSTED_VIDEO': '1', 'TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN': SECRET,
        'TRAFFICLIFT_VIDEO_DIR': str(tmp_path), 'TRAFFICLIFT_PUBLISHING_KEY': Fernet.generate_key().decode(),
        'TRAFFICLIFT_YOUTUBE_ENABLED': '1', 'YOUTUBE_CLIENT_ID': 'test-client', 'YOUTUBE_CLIENT_SECRET': 'test-secret',
        'YOUTUBE_REDIRECT_URI': HOST + '/api/v1/publishing/youtube/callback'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(api, 'campaign', lambda identifier: PRODUCT if identifier == PRODUCT['id'] else {'id': identifier})
    monkeypatch.setattr(api, 'inspect_media', lambda path: TECHNICAL)
    monkeypatch.setattr(youtube, 'call', Mock(side_effect=AssertionError('Unexpected network call')))
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app, base_url=HOST, headers={'x-trafficlift-video-token': SECRET, 'origin': HOST})
    connection = {'grant_id': 'grant-one', 'access_token': 'access-test-token', 'refresh_token': 'refresh-test-token',
                  'expires_at': time.time() + 3600, 'channel_id': CHANNEL,
                  'channels': [{'id': CHANNEL, 'title': 'Owner channel'}]}
    store.put('youtube', 'owner', connection)
    return client, connection, tmp_path


def draft(client, **changes):
    media = client.post('/api/v1/publishing/media?campaign_id=campaign-one', content=b'unchanged-existing-MP4',
                        headers={'content-type': 'video/mp4'})
    assert media.status_code == 200, media.text
    payload = {'media_id': media.json()['id'], 'campaign_id': PRODUCT['id'], 'channel_id': CHANNEL,
               'title': 'A Season for Everyone', 'description': 'Find the book through our channel profile.\n' + LINK,
               'marketing_url': LINK, 'visibility': 'private', 'made_for_kids': False, 'synthetic_media': True,
               'confirm_product_and_rights': True, 'confirm_video_and_copy': True, 'confirm_profile_link': True,
               'confirm_not_previously_posted': True, **changes}
    return client.post('/api/v1/publishing/jobs', json=payload)


def approve(client, job):
    return client.post('/api/v1/publishing/jobs/' + job['id'] + '/approve',
                       json={'approve': True, 'review_hash': job['review_hash']})


def details(job, privacy='private', processing='succeeded', **overrides):
    snapshot = job['snapshot']
    return {'items': [{'id': VIDEO, 'snippet': {'channelId': CHANNEL, 'title': snapshot['title'],
                       'description': snapshot['description']},
                       'status': {'uploadStatus': 'processed', 'privacyStatus': privacy,
                                  'selfDeclaredMadeForKids': False, 'containsSyntheticMedia': True, **overrides},
                       'processingDetails': {'processingStatus': processing}}]}


def fake_youtube(job, privacy='private', fail_final=False, resume=0):
    calls = []
    received = False

    def send(method, url, **kwargs):
        nonlocal received
        calls.append((method, url, kwargs))
        if url.endswith('/channels'):
            return response(value={'items': [{'id': CHANNEL, 'snippet': {'title': 'Owner channel'}}]})
        if url.endswith('/videos') and method == 'GET':
            return response(value=details(job, privacy))
        if method == 'POST':
            return response(headers={'Location': SESSION})
        if kwargs['headers']['Content-Range'].startswith('bytes */'):
            if received:
                return response(value={'id': VIDEO})
            return response(308, headers={'Range': f'bytes=0-{resume-1}'} if resume else {})
        received = True
        if fail_final:
            raise HTTPException(502, 'Upload response timed out.')
        return response(value={'id': VIDEO})
    return send, calls


def test_owner_and_same_origin_required(setup):
    client, _, _ = setup
    anonymous = TestClient(client.app, base_url=HOST)
    assert anonymous.get('/api/v1/publishing/status').status_code == 401
    assert anonymous.get('/api/v1/publishing/jobs').status_code == 401
    for path in ('youtube/connect', 'youtube/disconnect', 'jobs'):
        assert client.post('/api/v1/publishing/' + path, json={}, headers={'origin': 'https://evil.test'}).status_code in (403, 422)
    assert client.post('/api/v1/publishing/youtube/connect', json={}, headers={'origin': ''}).status_code == 403


def test_oauth_pkce_browser_binding_single_use_and_encryption(setup, monkeypatch):
    client, _, root = setup
    result = client.post('/api/v1/publishing/youtube/connect').json()
    query = parse_qs(urlsplit(result['authorization_url']).query)
    assert set(query['scope'][0].split()) == youtube.SCOPES
    assert query['code_challenge_method'] == ['S256']
    state = query['state'][0]
    pending = store.get('oauth', hashlib.sha256(state.encode()).hexdigest())
    expected = base64.urlsafe_b64encode(hashlib.sha256(pending['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert query['code_challenge'] == [expected]
    tokens = {'scope': ' '.join(youtube.SCOPES), 'access_token': 'new-access-secret', 'refresh_token': 'new-refresh-secret'}
    call = Mock(side_effect=[response(value=tokens), response(value={'items': [{'id': CHANNEL, 'snippet': {'title': 'Owner channel'}}]})])
    monkeypatch.setattr(youtube, 'call', call)
    result = client.get('/api/v1/publishing/youtube/callback', params={'state': state, 'code': 'secret-code'}, follow_redirects=False)
    assert result.status_code == 303
    assert call.call_args_list[0].kwargs['data']['code_verifier'] == pending['verifier']
    assert store.get('youtube', 'owner')['channel_id'] is None
    assert client.get('/api/v1/publishing/youtube/callback', params={'state': state, 'code': 'secret-code'}).status_code == 400
    stored = (root / 'publishing/publishing.sqlite3').read_bytes()
    for secret in (b'new-refresh-secret', b'new-access-secret', pending['verifier'].encode()):
        assert secret not in stored


@pytest.mark.parametrize('mode', ['browser', 'expired', 'denied', 'missing_state'])
def test_oauth_csrf_expiry_and_denial_never_exchange(setup, mode):
    client, _, _ = setup
    url = client.post('/api/v1/publishing/youtube/connect').json()['authorization_url']
    state = parse_qs(urlsplit(url).query)['state'][0]
    if mode == 'browser':
        client.cookies.clear()
    if mode == 'expired':
        key = hashlib.sha256(state.encode()).hexdigest()
        value = store.get('oauth', key)
        value['expires_at'] = 0
        store.put('oauth', key, value)
    result = client.get('/api/v1/publishing/youtube/callback', params={'state': '' if mode == 'missing_state' else state,
        'code': 'test-code', 'error': 'access_denied' if mode == 'denied' else ''})
    assert result.status_code == 400
    youtube.call.assert_not_called()


def test_missing_oauth_permission_keeps_previous_grant(setup, monkeypatch):
    client, old, _ = setup
    url = client.post('/api/v1/publishing/youtube/connect').json()['authorization_url']
    state = parse_qs(urlsplit(url).query)['state'][0]
    monkeypatch.setattr(youtube, 'call', Mock(return_value=response(value={
        'scope': 'https://www.googleapis.com/auth/youtube.upload', 'access_token': 'a', 'refresh_token': 'r'})))
    assert client.get('/api/v1/publishing/youtube/callback', params={'state': state, 'code': 'c'}).status_code == 409
    assert store.get('youtube', 'owner') == old


def test_refresh_failure_and_scope_reduction(setup, monkeypatch):
    _, connection, _ = setup
    connection['expires_at'] = 0
    store.put('youtube', 'owner', connection)
    monkeypatch.setattr(youtube, 'call', Mock(return_value=response(400)))
    with pytest.raises(HTTPException):
        youtube.credentials()
    assert store.get('youtube', 'owner')['expires_at'] == 0
    monkeypatch.setattr(youtube, 'call', Mock(return_value=response(value={'access_token': 'a', 'scope': ''})))
    with pytest.raises(HTTPException):
        youtube.credentials()


def test_refresh_rotation_is_encrypted_and_disconnect_is_durable(setup, monkeypatch):
    client, connection, _ = setup
    connection['expires_at'] = 0
    store.put('youtube', 'owner', connection)
    monkeypatch.setattr(youtube, 'call', Mock(return_value=response(value={'access_token': 'new', 'refresh_token': 'rotated'})))
    assert youtube.credentials()['refresh_token'] == 'rotated'
    monkeypatch.setattr(youtube, 'call', Mock(return_value=response(503)))
    assert client.post('/api/v1/publishing/youtube/disconnect').json() == {
        'connected': False, 'revoked': False,
        'message': 'Disconnected locally. Retry disconnect to finish Google revocation, or remove TrafficLift in Google account permissions.'}
    assert store.get('youtube', 'owner') is None
    assert store.records('revocation')[0]['refresh_token'] == 'rotated'
    monkeypatch.setattr(youtube, 'call', Mock(return_value=response()))
    assert api.revoke_pending()
    assert not store.records('revocation')


def test_verified_channel_selection_only(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(youtube, 'channels', lambda connection: [{'id': CHANNEL, 'title': 'Owner channel'}])
    assert client.post('/api/v1/publishing/youtube/channel', json={'channel_id': 'wrong'}).status_code == 409
    assert client.post('/api/v1/publishing/youtube/channel', json={'channel_id': CHANNEL}).status_code == 200


def test_disconnect_cancels_pending_oauth_and_cannot_be_undone_by_late_callback(setup, monkeypatch):
    client, _, _ = setup
    url = client.post('/api/v1/publishing/youtube/connect').json()['authorization_url']
    state = parse_qs(urlsplit(url).query)['state'][0]
    tokens = {'scope': ' '.join(youtube.SCOPES), 'access_token': 'new-access', 'refresh_token': 'new-refresh'}
    monkeypatch.setattr(youtube, 'call', Mock(return_value=response(value=tokens)))

    def disconnect_during_channel_lookup(connection):
        assert client.post('/api/v1/publishing/youtube/disconnect').status_code == 200
        return [{'id': CHANNEL, 'title': 'Owner channel'}]

    monkeypatch.setattr(youtube, 'channels', disconnect_during_channel_lookup)
    result = client.get('/api/v1/publishing/youtube/callback', params={'state': state, 'code': 'c'})
    assert result.status_code == 409
    assert store.get('youtube', 'owner') is None
    assert store.records('revocation')[0]['refresh_token'] == 'new-refresh'


def test_draft_has_no_upload_side_effect_and_media_identity_preserved(setup):
    client, _, root = setup
    result = draft(client)
    assert result.status_code == 200, result.text
    job = result.json()
    assert job['state'] == 'review'
    assert job['public_url'] is None
    assert 'grant_id' not in job['snapshot']
    preview = client.get('/api/v1/publishing/media/' + job['media_id'])
    assert preview.content == b'unchanged-existing-MP4'
    assert hashlib.sha256(preview.content).hexdigest() == job['snapshot']['media_hash']
    youtube.process_job(job['id'])
    youtube.call.assert_not_called()
    assert not list((root / 'publishing/media').glob('*.part'))


@pytest.mark.parametrize('change,code', [({'campaign_id': 'different'}, 409), ({'channel_id': 'wrong'}, 409),
    ({'marketing_url': 'https://www.amazon.com/dp/B012345678'}, 422),
    ({'description': 'Click the link below ' + LINK}, 422), ({'description': 'No product link'}, 422),
    ({'made_for_kids': 'false'}, 422), ({'synthetic_media': None}, 422),
    ({'confirm_video_and_copy': False}, 422), ({'confirm_not_previously_posted': False}, 422),
    ({'visibility': 'default'}, 422), ({'title': ' '}, 422)])
def test_review_rejects_product_copy_disclosure_and_visibility_errors(setup, change, code):
    client, _, _ = setup
    assert draft(client, **change).status_code == code
    youtube.call.assert_not_called()


def test_stale_review_rejected_draft_editable_and_approval_immutable(setup):
    client, _, _ = setup
    first = draft(client).json()
    revised = draft(client, title='Reviewed book title').json()
    assert first['id'] == revised['id']
    assert first['review_hash'] != revised['review_hash']
    assert approve(client, first).status_code == 409
    assert approve(client, revised).status_code == 200
    assert draft(client, title='Changed after approval').status_code == 409
    assert store.job(first['id'])['snapshot']['title'] == 'Reviewed book title'


def test_concurrent_duplicate_clicks_and_worker_claims(setup):
    client, _, _ = setup
    job = draft(client).json()
    assert draft(client).json()['id'] == job['id']
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: store.approve(job['id'], job['review_hash']), range(4)))
    assert all(value['approved_at'] == results[0]['approved_at'] for value in results)
    with ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda _: store.claim(job['id']), range(4)))
    assert sum(value is not None for value in claims) == 1
    assert len(store.jobs()) == 1


@pytest.mark.parametrize('requested,actual,expected', [('public', 'private', 'private'),
    ('public', 'public', 'public'), ('private', 'private', 'private'), ('unlisted', 'unlisted', 'unlisted')])
def test_resumable_upload_actual_visibility_and_public_url(setup, monkeypatch, requested, actual, expected):
    client, _, _ = setup
    job = draft(client, visibility=requested).json()
    approve(client, job)
    send, calls = fake_youtube(job, actual)
    monkeypatch.setattr(youtube, 'call', send)
    youtube.process_job(job['id'])
    value = client.get('/api/v1/publishing/jobs/' + job['id']).json()
    assert value['state'] == expected
    assert value['actual_visibility'] == actual
    assert bool(value['public_url']) == (actual == 'public')
    assert value['video_id'] == VIDEO
    inserts = [kwargs for method, _, kwargs in calls if method == 'POST']
    assert len(inserts) == 1
    assert inserts[0]['json']['status']['privacyStatus'] == requested
    assert inserts[0]['json']['status']['containsSyntheticMedia'] is True
    assert [kwargs['data'] for method, _, kwargs in calls if method == 'PUT' and kwargs['data']] == [b'unchanged-existing-MP4']
    assert 'test-session' not in json.dumps(value)
    assert 'access-test-token' not in json.dumps(value)
    assert approve(client, job).json()['video_id'] == VIDEO
    assert len(store.jobs()) == 1


def test_lost_final_response_reconciles_without_second_insert(setup, monkeypatch):
    client, _, _ = setup
    job = draft(client).json()
    approve(client, job)
    send, calls = fake_youtube(job, fail_final=True)
    monkeypatch.setattr(youtube, 'call', send)
    youtube.process_job(job['id'])
    assert store.job(job['id'])['state'] == 'reconciling'
    assert store.job(job['id'])['session'] == SESSION
    youtube.process_job(job['id'])
    assert store.job(job['id'])['video_id'] == VIDEO
    assert len([method for method, _, _ in calls if method == 'POST']) == 1
    assert len([kwargs for method, _, kwargs in calls if method == 'PUT' and kwargs['data']]) == 1


def test_restart_resumes_server_offset_same_session(setup, monkeypatch):
    client, _, _ = setup
    job = draft(client).json()
    approve(client, job)
    value = store.job(job['id'])
    value.update(session=SESSION, session_started=True, state='uploading')
    store.save_job(value, release=True)
    send, calls = fake_youtube(job, resume=8)
    monkeypatch.setattr(youtube, 'call', send)
    youtube.process_job(job['id'])
    assert store.job(job['id'])['video_id'] == VIDEO
    assert not [method for method, _, _ in calls if method == 'POST']
    chunks = [kwargs for method, _, kwargs in calls if method == 'PUT' and kwargs['data']]
    assert chunks[0]['data'] == b'unchanged-existing-MP4'[8:]


def test_session_expired_or_unknown_initiation_never_reinserts(setup, monkeypatch):
    client, _, _ = setup
    job = draft(client).json()
    approve(client, job)
    value = store.job(job['id'])
    value.update(session_started=True, state='uploading')
    store.save_job(value, release=True)
    youtube.process_job(job['id'])
    assert store.job(job['id'])['state'] == 'needs_attention'
    youtube.call.assert_not_called()
    value.update(session=SESSION, state='reconciling')
    store.save_job(value, release=True)
    mock = Mock(return_value=response(404))
    monkeypatch.setattr(youtube, 'call', mock)
    youtube.process_job(job['id'])
    assert store.job(job['id'])['state'] == 'needs_attention'
    assert mock.call_args.args[0] == 'PUT'


def test_owner_can_resume_same_channel_after_reconnect_without_changing_approval(setup, monkeypatch):
    client, connection, _ = setup
    job = draft(client).json()
    approve(client, job)
    saved = store.job(job['id'])
    saved.update(session=SESSION, session_started=True, state='needs_attention')
    store.save_job(saved, release=True)
    connection['grant_id'] = 'renewed-grant'
    store.put('youtube', 'owner', connection)
    monkeypatch.setattr(youtube, 'channels', lambda _: [{'id': CHANNEL, 'title': 'Owner channel'}])
    result = client.post('/api/v1/publishing/jobs/' + job['id'] + '/reconcile',
                         json={'approve': True, 'review_hash': job['review_hash']})
    assert result.status_code == 200, result.text
    assert store.job(job['id'])['snapshot'] == saved['snapshot']
    assert store.job(job['id'])['active_grant_id'] == 'renewed-grant'
    send, calls = fake_youtube(job)
    monkeypatch.setattr(youtube, 'call', send)
    youtube.process_job(job['id'])
    assert store.job(job['id'])['video_id'] == VIDEO
    assert not any(method == 'POST' for method, _, _ in calls)


def test_reconcile_rejects_different_channel_and_allows_pre_upload_recovery(setup, monkeypatch):
    client, connection, _ = setup
    job = draft(client).json()
    approve(client, job)
    connection['channel_id'] = 'different'
    store.put('youtube', 'owner', connection)
    monkeypatch.setattr(youtube, 'channels', lambda _: [{'id': CHANNEL, 'title': 'Owner channel'}])
    url = '/api/v1/publishing/jobs/' + job['id'] + '/reconcile'
    payload = {'approve': True, 'review_hash': job['review_hash']}
    assert client.post(url, json=payload).status_code == 409
    connection['channel_id'] = CHANNEL
    connection['grant_id'] = 'renewed-grant'
    store.put('youtube', 'owner', connection)
    assert client.post(url, json=payload).status_code == 200
    send, calls = fake_youtube(job)
    monkeypatch.setattr(youtube, 'call', send)
    youtube.process_job(job['id'])
    assert store.job(job['id'])['video_id'] == VIDEO
    assert sum(method == 'POST' for method, _, _ in calls) == 1


@pytest.mark.parametrize('case', ['changed_file', 'disconnected', 'changed_channel', 'new_grant'])
def test_approved_upload_stops_on_media_or_account_change(setup, case):
    client, connection, root = setup
    job = draft(client).json()
    approve(client, job)
    if case == 'changed_file':
        (root / 'publishing/media' / (job['snapshot']['media_hash'] + '.mp4')).write_bytes(b'changed')
    elif case == 'disconnected':
        store.pop('youtube', 'owner')
    else:
        connection['channel_id' if case == 'changed_channel' else 'grant_id'] = 'different'
        store.put('youtube', 'owner', connection)
    youtube.process_job(job['id'])
    assert store.job(job['id'])['state'] == 'needs_attention'
    youtube.call.assert_not_called()


@pytest.mark.parametrize('case', ['processing', 'wrong_channel', 'wrong_disclosure', 'failed', 'missing'])
def test_verification_never_claims_public_without_all_checks(setup, monkeypatch, case):
    client, connection, _ = setup
    job = draft(client, visibility='public').json()
    approve(client, job)
    value = store.job(job['id'])
    value['video_id'] = VIDEO
    result = details(job, privacy='public')
    if case == 'processing':
        result['items'][0]['processingDetails']['processingStatus'] = 'processing'
    elif case == 'wrong_channel':
        result['items'][0]['snippet']['channelId'] = 'wrong'
    elif case == 'wrong_disclosure':
        result['items'][0]['status']['containsSyntheticMedia'] = False
    elif case == 'failed':
        result['items'][0]['status']['uploadStatus'] = 'rejected'
    else:
        result['items'] = []
    monkeypatch.setattr(youtube, 'api_get', lambda *args, **kwargs: result)
    youtube.verify(value, connection)
    assert youtube.public_job(value)['public_url'] is None


def test_media_limits_cleanup_and_owner_only_preview(setup, monkeypatch):
    client, _, root = setup
    monkeypatch.setattr(api, 'MAX_MEDIA', 3)
    result = client.post('/api/v1/publishing/media?campaign_id=campaign-one', content=b'too large', headers={'content-type': 'video/mp4'})
    assert result.status_code == 413
    assert not list((root / 'publishing/media').glob('*.part'))
    assert client.post('/api/v1/publishing/media?campaign_id=campaign-one', content=b'x').status_code == 415


def test_real_probe_rejects_wrong_codec_audio_ratio_and_corruption(setup, monkeypatch):
    # Exercise ffprobe and decode command handling independently of the mocked import fixture.
    original = INSPECT_MEDIA
    monkeypatch.setattr(api, '_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(api.shutil, 'which', lambda name: 'ffprobe')
    valid = {'format': {'duration': '15', 'format_name': 'mov,mp4'}, 'streams': [
        {'codec_type': 'video', 'codec_name': 'h264', 'width': 720, 'height': 1280},
        {'codec_type': 'audio', 'codec_name': 'aac'}]}
    run = Mock(side_effect=[Mock(stdout=json.dumps(valid)), Mock()])
    monkeypatch.setattr(api.subprocess, 'run', run)
    assert original(Path('original.mp4'))['width'] == 720
    assert '-f' in run.call_args.args[0] and 'null' in run.call_args.args[0]
    for changes in ('ratio', 'audio', 'codec', 'duration'):
        value = json.loads(json.dumps(valid))
        if changes == 'ratio': value['streams'][0]['width'] = 1280
        if changes == 'audio': value['streams'].pop()
        if changes == 'codec': value['streams'][0]['codec_name'] = 'hevc'
        if changes == 'duration': value['format']['duration'] = '181'
        monkeypatch.setattr(api.subprocess, 'run', Mock(return_value=Mock(stdout=json.dumps(value))))
        with pytest.raises(HTTPException):
            original(Path('invalid.mp4'))


def test_resumable_session_rejects_credential_exfiltration_targets():
    for url in ('http://www.googleapis.com/upload/youtube/v3/videos', 'https://evil.test/upload/youtube/v3/videos',
                'https://www.googleapis.com.evil.test/upload/youtube/v3/videos',
                'https://user:pass@www.googleapis.com/upload/youtube/v3/videos'):
        with pytest.raises(HTTPException):
            youtube.session_url(url)


def test_real_existing_mp4_import_decode_and_hash(setup, monkeypatch, tmp_path):
    import subprocess
    from backend.social_exports_api import _ffmpeg
    client, _, _ = setup
    source = tmp_path / 'owned-fixture.mp4'
    subprocess.run([_ffmpeg(), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=navy:s=90x160:r=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '0.5',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)],
                   capture_output=True, check=True, timeout=30)
    monkeypatch.setattr(api, 'inspect_media', INSPECT_MEDIA)
    original = source.read_bytes()
    result = client.post('/api/v1/publishing/media?campaign_id=campaign-one', content=original,
                         headers={'content-type': 'video/mp4'})
    assert result.status_code == 200, result.text
    assert result.json()['media_hash'] == hashlib.sha256(original).hexdigest()
    preview = client.get('/api/v1/publishing/media/' + result.json()['id'])
    assert preview.content == original
    anonymous = TestClient(client.app, base_url=HOST)
    assert anonymous.get('/api/v1/publishing/media/' + result.json()['id']).status_code == 401

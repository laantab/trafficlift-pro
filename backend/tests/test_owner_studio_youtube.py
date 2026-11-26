"""Owner Studio campaigns queue their own verified video for YouTube. No network."""
import json
import time
import zipfile
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from backend import campaign_api, campaign_editor
from backend import publishing_api as api
from backend import publishing_store as store

CHANNEL = 'UC' + 'a' * 22
TECHNICAL = {'width': 1080, 'height': 1920, 'duration': 16.0, 'audio_codec': 'aac', 'video_codec': 'h264'}


def test_campaign_short_is_queued_once_with_its_own_copy(tmp_path, monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_VIDEO_DIR', str(tmp_path))
    monkeypatch.setenv('TRAFFICLIFT_PUBLISHING_KEY', Fernet.generate_key().decode())
    monkeypatch.setattr(api, 'guard', lambda request, mutation=False: None)
    monkeypatch.setattr(api, 'inspect_media', lambda path: TECHNICAL)
    store.put('youtube', 'owner', {'grant_id': 'g1', 'access_token': 'a', 'refresh_token': 'r',
              'expires_at': time.time() + 3600, 'channel_id': CHANNEL, 'channels': [{'id': CHANNEL, 'title': 'Owner'}]})
    job = uuid4()
    folder = campaign_api.root() / job.hex
    folder.mkdir()
    (folder / 'video.mp4').write_bytes(b'approved video')
    with zipfile.ZipFile(folder / 'campaign.zip', 'w') as archive:
        archive.writestr('cover-frame.jpg', b'cover')
    link = campaign_api.HOLIDAY['destination']
    (folder / 'shorts.json').write_text(json.dumps({'title': 'A Season for Everyone | A Christmas memory',
        'description': 'A Season for Everyone.\n' + link}), encoding='utf-8')
    with campaign_api.database() as db:
        db.execute('INSERT INTO jobs VALUES(?,?,?,?)', (job.hex, 'holiday', 0, 'ready'))
    choice = api.CampaignShort(visibility='public', made_for_kids=False, synthetic_media=False, confirm=True)
    with pytest.raises(HTTPException):          # no editor receipt yet
        api.publish_campaign_short(job, choice, None)
    campaign_editor.receipt(folder, 10, 4)
    assert json.loads(api.campaign_short_status(job, None).body) == {}
    first = api.publish_campaign_short(job, choice, None)
    again = api.publish_campaign_short(job, choice, None)
    assert first['state'] == 'queued' and again['id'] == first['id'] and len(store.jobs()) == 1
    snapshot = first['snapshot']
    assert snapshot['title'].startswith('A Season for Everyone') and link in snapshot['description']
    assert snapshot['channel_id'] == CHANNEL and 'grant_id' not in snapshot
    assert (tmp_path / 'publishing' / 'media' / (snapshot['media_hash'] + '.mp4')).read_bytes() == b'approved video'
    assert json.loads(api.campaign_short_status(job, None).body)['id'] == first['id']
    private = api.CampaignShort(visibility='private', made_for_kids=False, synthetic_media=False, confirm=True)
    with pytest.raises(HTTPException):          # the same video cannot be queued twice with new settings
        api.publish_campaign_short(job, private, None)

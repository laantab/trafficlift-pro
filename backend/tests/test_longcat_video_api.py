"""Cost and product-integrity boundaries for the LongCat trial (no paid calls)."""
from unittest.mock import Mock, patch
import pytest
from fastapi import HTTPException
import fal_client
from backend import longcat_video_api as api
from backend.photo_video_api import VideoRequest

JOB = '019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b'
PHOTO = 'https://target.scene7.com/is/image/Target/verified-lamp'

@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('FAL_KEY', 'test-key')
    monkeypatch.setenv('TRAFFICLIFT_LONGCAT_VIDEO_ENABLED', '1')


def body(approved=True):
    return api.LongCatSubmit(product=VideoRequest(
        name='Desk lamp', image_url=PHOTO,
        product_url='https://www.target.com/p/desk-lamp'),
        approve_paid_generation=approved)


def test_default_disabled(monkeypatch):
    monkeypatch.delenv('TRAFFICLIFT_LONGCAT_VIDEO_ENABLED', raising=False)
    with patch.object(api, 'video_guard'), patch.object(api.fal_client, 'submit') as send:
        with pytest.raises(HTTPException) as error:
            api.submit(body(), Mock())
    assert error.value.status_code == 503
    send.assert_not_called()


def test_owner_gate_precedes_any_provider_call(enabled):
    with patch.object(api, 'video_guard', side_effect=HTTPException(401, 'Owner required')), \
         patch.object(api.fal_client, 'submit') as send:
        with pytest.raises(HTTPException) as error:
            api.submit(body(), Mock())
    assert error.value.status_code == 401
    send.assert_not_called()


def test_no_approval_cannot_spend(enabled):
    with patch.object(api, 'video_guard'), patch.object(api.fal_client, 'submit') as send:
        with pytest.raises(HTTPException) as error:
            api.submit(body(False), Mock())
    assert error.value.status_code == 409
    send.assert_not_called()


def test_missing_verified_photo_cannot_spend(enabled):
    with patch.object(api, 'video_guard'), \
         patch.object(api, 'prepare_video_plan', return_value=({}, [])), \
         patch.object(api, 'marketing_readiness_gate'), \
         patch.object(api.fal_client, 'submit') as send:
        with pytest.raises(HTTPException) as error:
            api.submit(body(), Mock())
    assert error.value.status_code == 422
    send.assert_not_called()


def test_one_fixed_price_clip_from_verified_photo(enabled):
    with patch.object(api, 'video_guard'), \
         patch.object(api, 'prepare_video_plan', return_value=({}, [PHOTO])), \
         patch.object(api, 'marketing_readiness_gate'), \
         patch.object(api.fal_client, 'submit', return_value=Mock(request_id=JOB)) as send:
        result = api.submit(body(), Mock())
    send.assert_called_once()
    assert send.call_args.args == (api.MODEL,)
    payload = send.call_args.kwargs['arguments']
    assert payload['image_url'] == PHOTO
    assert payload['num_frames'] == 162 and payload['fps'] == 30
    assert 'aspect_ratio' not in payload and 'resolution' not in payload
    assert payload['enable_safety_checker'] is True
    assert payload['enable_prompt_expansion'] is False
    assert result['estimated_usd'] == pytest.approx(162 / 30 * .01)


def test_uncertain_submission_never_retried_or_leaked(enabled):
    with patch.object(api, 'video_guard'), \
         patch.object(api, 'prepare_video_plan', return_value=({}, [PHOTO])), \
         patch.object(api, 'marketing_readiness_gate'), \
         patch.object(api.fal_client, 'submit', side_effect=RuntimeError('secret provider body')) as send:
        with pytest.raises(HTTPException) as error:
            api.submit(body(), Mock())
    assert 'secret' not in error.value.detail
    send.assert_called_once()


@pytest.mark.parametrize('state,expected', [
    (fal_client.Queued(position=0), 'in_queue'),
    (fal_client.InProgress(logs=None), 'in_progress'),
    (fal_client.Completed(logs=None, metrics={}), 'completed'),
])
def test_poll_reuses_job_without_new_submission(enabled, state, expected):
    handle = Mock()
    handle.status.return_value = state
    handle.get.return_value = {'video': {'url': 'https://cdn.example.org/longcat.mp4'}}
    with patch.object(api, 'video_guard'), \
         patch.object(api.fal_client, 'SyncClient') as client, \
         patch.object(api.fal_client, 'submit') as send:
        client.return_value.get_handle.return_value = handle
        result = api.status(JOB, Mock())
    assert result['status'] == expected
    client.return_value.get_handle.assert_called_once_with(api.MODEL, JOB)
    send.assert_not_called()


def test_invalid_job_id_never_contacts_provider(enabled):
    with patch.object(api, 'video_guard'), patch.object(api.fal_client, 'SyncClient') as client:
        with pytest.raises(HTTPException):
            api.status('https://evil.example/job', Mock())
    client.assert_not_called()

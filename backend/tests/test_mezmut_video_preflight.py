"""Customer-reproduced 2026-10-29 MEZMUT Video Studio regression.

All seller claims in the positive test are fixtures, NOT verified claims
about Amazon ASIN B0GVF21RZG.
"""
from unittest.mock import patch
import pytest
from fastapi import HTTPException

from backend.photo_video_api import VideoRequest, create, prepare_video_plan, worker
from backend.sales_script import creative_copy_gate, marketing_readiness_gate

TITLE = 'MEZMUT 20 oz Insulated Tumbler Cup Travel Coffee Mug with Lid(Mixed1,6Pack)'
URL = 'https://www.amazon.com/dp/B0GVF21RZG'


def request(**overrides):
    data = dict(name=TITLE, product_url=URL,
                image_url='https://m.media-amazon.com/images/I/fixture.jpg',
                benefit='# ' + TITLE,
                buyer_need='Looking for a useful addition to your daily routine',
                consideration='Review listed features',
                fact_sources=[{'title': TITLE, 'url': URL, 'snippet': '# ' + TITLE}])
    data.update(overrides)
    return VideoRequest.model_construct(**data)


def test_reproduced_title_only_mezmut_becomes_claim_safe_overview():
    payload = request()
    with patch('backend.photo_video_api.resolve_video_facts', return_value={}):
        plan, photos = prepare_video_plan(payload)
    assert plan['mode'] == 'product_overview'
    assert plan['evidence'] == []
    assert photos == []
    gate=marketing_readiness_gate(plan)
    assert gate['status']=='PASS'
    assert gate['claims']=='identity_and_photo_only'
    assert 'MEZMUT' in plan['product']


def test_video_post_accepts_title_only_as_claim_safe_overview(tmp_path, monkeypatch):
    payload = request()
    monkeypatch.setenv('TRAFFICLIFT_VIDEO_DIR', str(tmp_path))
    with patch('backend.photo_video_api.video_guard'), \
         patch('backend.photo_video_api.resolve_video_facts', return_value={}), \
         patch('backend.photo_video_api.dependencies') as dependencies, \
         patch('backend.photo_video_api.reserve_hosted_render') as reserve, \
         patch('backend.photo_video_api.threading.Thread') as thread, \
         patch('backend.photo_video_api.lock') as lock:
        lock.acquire.return_value=True
        response=create(payload, request=None)
    assert response.status_code == 202
    dependencies.assert_called_once()
    reserve.assert_called_once()
    thread.assert_called_once()


def test_worker_renders_title_only_as_claim_safe_overview(tmp_path):
    payload = request()
    record = {'id': 'f' * 32, 'status': 'queued'}
    photo=tmp_path/'photo.jpg'
    photo.write_bytes(b'fixture')
    with patch('backend.photo_video_api.resolve_video_facts', return_value={}), \
         patch('backend.photo_video_api._download_product_image', return_value=photo) as download, \
         patch('backend.photo_quality.choose_photos', return_value=[(photo, {'status':'SOURCE_CHECKS_PASS','concerns':[]})]), \
         patch('backend.photo_video_api.render') as render, \
         patch('backend.photo_video_api.lock') as lock:
        worker(record, payload, tmp_path)
    assert record['status'] == 'succeeded'
    assert record['sales_plan']['mode'] == 'product_overview'
    assert 'Product overview rendered' in record['message']
    assert record['video_url']
    download.assert_called()
    render.assert_called_once()
    lock.release.assert_called_once()


def test_two_exact_listing_fact_fixtures_survive_title_only_override():
    payload = request(fact_sources=[{
        'title': TITLE, 'url': URL,
        'snippet': 'Includes a stainless steel straw. Lid with silicone seal for a snug fit.'
    }])
    with patch('backend.photo_video_api.resolve_video_facts') as lookup:
        plan, _ = prepare_video_plan(payload)
    lookup.assert_not_called()
    assert plan['mode'] == 'benefit_led'
    assert len(plan['evidence']) == 2
    assert marketing_readiness_gate(plan)['distinct_product_facts'] == 2
    assert 'MEZMUT' in ' '.join(plan['captions'])
    assert all('Looking for a useful addition' not in line for line in plan['phrases'])
    assert all('Review listed features' not in line for line in plan['phrases'])


def test_generic_seller_snippets_do_not_count_as_facts_but_allow_safe_overview():
    payload = request(fact_sources=[{
        'title': TITLE, 'url': URL,
        'snippet': 'Review listed features. Check the seller listing for exact options.'
    }])
    with patch('backend.photo_video_api.resolve_video_facts', return_value={}):
        plan, _ = prepare_video_plan(payload)
    assert plan['mode'] == 'product_overview'
    gate=marketing_readiness_gate(plan)
    assert gate['distinct_product_facts']==0
    assert gate['claims']=='identity_and_photo_only'


def test_real_model_mismatch_remains_rejected():
    plan = {
        'product': 'JAS11242 LED Motion-Sensor Night Light Nickel Finish',
        'phrases': ['Considering JAS11242 LED Motion-Sensor Night Light Nickel Finish?',
                    'The seller lists a nickel finish.',
                    'Tap the product link to compare options.'],
        'captions': ['JAS12201 Motion-Sensor Night Light', 'Nickel finish', 'See listing']
    }
    with pytest.raises(ValueError, match='exact product model'):
        creative_copy_gate(plan)

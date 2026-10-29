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


def test_reproduced_title_only_mezmut_has_specific_evidence_error_not_placeholder():
    payload = request()
    with patch('backend.photo_video_api.resolve_video_facts', return_value={}):
        plan, photos = prepare_video_plan(payload)
    assert plan['mode'] == 'product_overview'
    assert plan['evidence'] == []
    assert photos == []
    with pytest.raises(ValueError, match='Supply two distinct facts from the exact seller listing') as error:
        marketing_readiness_gate(plan)
    assert 'Generic placeholder' not in str(error.value)
    assert 'MEZMUT' in plan['product']


def test_video_post_rejects_title_only_before_dependency_or_capacity_or_render():
    payload = request()
    with patch('backend.photo_video_api.video_guard'), \
         patch('backend.photo_video_api.resolve_video_facts', return_value={}), \
         patch('backend.photo_video_api.dependencies') as dependencies, \
         patch('backend.photo_video_api.reserve_hosted_render') as reserve:
        with pytest.raises(HTTPException) as error:
            create(payload, request=None)
    assert error.value.status_code == 422
    assert 'two distinct facts from the exact seller listing' in error.value.detail
    assert 'Generic placeholder' not in error.value.detail
    dependencies.assert_not_called()
    reserve.assert_not_called()


def test_worker_reports_specific_failure_without_downloading_or_rendering(tmp_path):
    payload = request()
    record = {'id': 'f' * 32, 'status': 'queued'}
    with patch('backend.photo_video_api.resolve_video_facts', return_value={}), \
         patch('backend.photo_video_api._download_product_image') as download, \
         patch('backend.photo_video_api.render') as render, \
         patch('backend.photo_video_api.lock') as lock:
        worker(record, payload, tmp_path)
    assert record['status'] == 'failed'
    assert record['sales_plan']['mode'] == 'product_overview'
    assert 'two distinct facts from the exact seller listing' in record['message']
    assert 'Generic placeholder' not in record['message']
    assert record['video_url'] is None
    download.assert_not_called()
    render.assert_not_called()
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


def test_generic_seller_snippets_do_not_count_as_two_facts():
    payload = request(fact_sources=[{
        'title': TITLE, 'url': URL,
        'snippet': 'Review listed features. Check the seller listing for exact options.'
    }])
    with patch('backend.photo_video_api.resolve_video_facts', return_value={}):
        plan, _ = prepare_video_plan(payload)
    assert plan['mode'] == 'product_overview'
    with pytest.raises(ValueError, match='two distinct facts'):
        marketing_readiness_gate(plan)


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

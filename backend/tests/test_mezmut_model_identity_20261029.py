"""Customer-reproduced 2026-10-29 MEZMUT model-caption failure.

The positive seller claims are fixtures, not verified MEZMUT listing claims.
No test reserves render capacity, downloads photos, or generates an MP4.
"""
from unittest.mock import patch
import pytest
from fastapi import HTTPException

from backend.photo_video_api import VideoRequest, create, prepare_video_plan
from backend.sales_script import (
    creative_copy_gate, identity_caption, is_model_identifier,
    marketing_readiness_gate, build_sales_plan,
)

TITLE = 'MEZMUT 20 oz Insulated Tumbler Cup Travel Coffee Mug with Lid(Mixed1,6Pack)'
URL = 'https://www.amazon.com/dp/B0GVF21RZG'


@pytest.mark.parametrize('title', [
    TITLE,
    TITLE + ' B0GVF21RZG',
    'MEZMUT 20oz6Pack Insulated Tumbler Cup Travel Coffee Mug Mixed1',
    'MEZMUT 20-oz Insulated Tumbler Cup (Mixed-1, 6-Pack)',
    'MEZMUT 20OZ Insulated Tumbler Cup Mixed1 6Pack B0GVF21RZG',
])
def test_mezmut_real_request_title_variations_do_not_require_catalog_options_as_models(title):
    plan = {
        'product': title,
        'phrases': [f'Considering {title}?',
                    'Check the exact seller listing for verified product details.',
                    'Tap the product link to compare options.'],
        'captions': ['MEZMUT Insulated Tumbler Cup',
                     'MEZMUT travel coffee mug', 'View product details'],
    }
    assert creative_copy_gate(plan)['status'] == 'PASS'


@pytest.mark.parametrize('token', ['20oz', 'Mixed1', '6Pack', '20oz6Pack',
                                   'B0GVF21RZG', '6PK', 'Mixed2'])
def test_catalog_option_or_asin_is_not_model(token):
    assert not is_model_identifier(token)


def test_real_sku_at_end_survives_short_caption_and_wrong_sku_is_refused():
    name = 'ACME Motion Sensor Night Light JAS11242'
    short = identity_caption(name, 38)
    assert 'JAS11242' in short
    plan = {
        'product': name,
        'phrases': [f'Considering {name}?',
                    'Seller lists a nickel finish.',
                    'Tap the product link to compare options.'],
        'captions': [short, 'Nickel finish', 'View product details'],
    }
    assert creative_copy_gate(plan)['status'] == 'PASS'
    plan['captions'][0] = 'ACME JAS12201 Motion Sensor Night Light'
    with pytest.raises(ValueError, match='exact product model'):
        creative_copy_gate(plan)


def request(**changes):
    data = dict(
        name=TITLE + ' B0GVF21RZG',
        image_url='https://m.media-amazon.com/images/I/fixture.jpg',
        product_url=URL,
        benefit='# ' + TITLE,
        buyer_need='Looking for a useful addition to your daily routine',
        consideration='Review listed features',
        fact_sources=[{'title': TITLE, 'url': URL, 'snippet': '# ' + TITLE}],
        seconds=15, style='warm',
    )
    data.update(changes)
    return VideoRequest.model_construct(**data)


def test_actual_request_shape_returns_precise_422_before_any_render_resources():
    payload = request()
    with patch('backend.photo_video_api.video_guard'), \\
         patch('backend.photo_video_api.resolve_video_facts', return_value={}), \\
         patch('backend.photo_video_api.dependencies') as dependencies, \\
         patch('backend.photo_video_api.reserve_hosted_render') as reserve:
        with pytest.raises(HTTPException) as err:
            create(payload, request=None)
    assert err.value.status_code == 422
    assert 'two distinct facts from the exact seller listing' in err.value.detail
    assert 'On-screen captions' not in err.value.detail
    assert 'Generic placeholder' not in err.value.detail
    dependencies.assert_not_called()
    reserve.assert_not_called()


def test_sourced_fact_fixture_creates_identity_aware_plan_without_unsupported_claims():
    payload = request(name=TITLE, fact_sources=[{
        'title': TITLE, 'url': URL,
        'snippet': 'Includes a stainless steel straw. Lid with silicone seal for a snug fit.'
    }])
    with patch('backend.photo_video_api.resolve_video_facts') as lookup:
        plan, _ = prepare_video_plan(payload, require_renderable=True)
    lookup.assert_not_called()
    assert marketing_readiness_gate(plan)['status'] == 'PASS'
    assert 'MEZMUT' in ' '.join(plan['captions'])
    assert 'Generic placeholder' not in ' '.join(plan['phrases'])
    assert len(plan['evidence']) == 2


def test_old_saved_plan_with_asin_in_title_and_two_facts_passes_current_gate():
    name = TITLE + ' B0GVF21RZG'
    plan = build_sales_plan(
        name, 'Includes a stainless steel straw', URL, 60,
        feature_details=['Lid with silicone seal for a snug fit'],
        evidence_origin='matched_seller_page',
    )
    assert marketing_readiness_gate(plan)['status'] == 'PASS'
    assert all('B0GVF21RZG' not in caption for caption in plan['captions'])


def test_title_only_benefit_remains_nonfact_when_asin_is_appended():
    from backend.sales_script import is_distinct_product_fact
    assert not is_distinct_product_fact('# ' + TITLE, TITLE + ' B0GVF21RZG')
    assert not is_distinct_product_fact(TITLE, TITLE)

from unittest.mock import patch
from pathlib import Path
from backend.product_facts import resolve_video_facts
from backend.sales_script import build_sales_plan
from backend.photo_video import narration_starts
from backend import listing_photo
from backend.photo_video_api import worker, VideoRequest
import json


def test_oura_full_script_has_reason_to_buy_and_purchase_details():
    with patch('backend.product_facts.listing_images') as lookup:
        facts = resolve_video_facts('Oura Ring 4 : Target', 'https://www.target.com/p/oura-ring-4')
        plan = build_sales_plan('Oura Ring 4 : Target', facts.pop('benefit'),
                               'https://www.target.com/p/oura-ring-4', 60, **facts)
    lookup.assert_not_called()
    assert plan['mode'] == 'benefit_led'
    assert 'sleep and daily activity?' in plan['phrases'][0]
    assert 'sleep and activity' in plan['phrases'][1].lower()
    assert 'Oura Membership' in plan['phrases'][1]
    assert 'sizing kit' in plan['phrases'][3]
    assert ': Target' not in ' '.join(plan['phrases'])
    assert plan['evidence'][0]['source_url'].startswith('https://ouraring.com/')
    assert 'not a live lookup' in plan['review']['evidence_status']


def test_ring_facts_do_not_apply_to_accessories_or_different_models():
    with patch('backend.product_facts.listing_images'):
        for name in ['Oura Ring 4 Sizing Kit', 'Oura Ring 3', 'Oura Ring 5', 'Oura Ring 4 Charger']:
            assert resolve_video_facts(name, 'https://www.target.com/p/item') == {}


def test_matched_meta_facts_are_retrieved_without_jsonld():
    html = '<title>Orbit Desk Lamp</title><meta name="description" content="Adjustable arm for positioning the light. Shop now for $99.">'
    with patch.object(listing_photo, '_safe_get', return_value=('https://shop.example/lamp',html,None)):
        facts = resolve_video_facts('Orbit Desk Lamp', 'https://shop.example/lamp')
        assert facts['benefit'] == 'Adjustable arm for positioning the light.'
        assert facts['evidence_origin'] == 'matched_seller_page'
        assert resolve_video_facts('Other Desk Lamp', 'https://shop.example/lamp') == {}


def test_facts_preserve_qualifications_and_skip_overlong_sentences():
    assert not listing_photo.usable_facts('Designed for '+ 'word '*40 + 'except in rain.')
    assert listing_photo.usable_facts('Fits most holders, except narrow ones.') == ['Fits most holders, except narrow ones.']


def test_cta_ends_near_video_end_instead_of_early_silent_hold():
    starts = narration_starts([2,4,3],30)
    assert starts[2] > 25
    assert abs(30 - (starts[2]+3) - 1.2) < .001
    assert starts[1] > starts[0]+2


def test_worker_rehydrates_history_product_and_extends_short_runtime(tmp_path):
    payload = VideoRequest.model_construct(name='Oura Ring 4 : Target',image_url='https://example.com/ring.jpg',
                           product_url='https://www.target.com/p/oura-ring-4',seconds=15)
    plan = build_sales_plan(payload.name,'',payload.product_url,15)
    record = dict(id='c'*32,status='queued',seconds=15,sales_plan=plan)
    with patch('backend.photo_video_api._download_product_image',return_value=tmp_path/'photo.jpg'), \
         patch('backend.photo_video_api.render') as render, \
         patch('backend.photo_video_api.lock') as lock:
        worker(record,payload,tmp_path)
    assert record['status']=='succeeded'
    assert record['sales_plan']['mode']=='benefit_led'
    assert record['seconds'] >= 30
    assert render.call_args.args[5] == record['seconds']
    assert render.call_args.kwargs['sales_plan']['phrases'][1].count('Oura Ring 4') >= 1
    assert json.loads((tmp_path/'job.json').read_text())['sales_plan']['evidence']
    lock.release.assert_called_once()


def test_missing_seller_details_completes_five_scene_overview(tmp_path):
    payload=VideoRequest.model_construct(name='Orbit Desk Lamp',image_url='https://example.com/lamp.jpg',
                         product_url='https://seller.example/products/lamp')
    record={'id':'d'*32,'status':'queued'}
    with patch('backend.photo_video_api.resolve_video_facts',return_value={}), \
         patch('backend.photo_video_api._download_product_image',return_value=tmp_path/'photo.jpg'), \
         patch('backend.photo_video_api.render') as render, patch('backend.photo_video_api.lock') as lock:
        worker(record,payload,tmp_path)
    assert record['status']=='succeeded'
    assert record['sales_plan']['mode']=='product_overview'
    assert len(record['sales_plan']['phrases'])==5
    assert record['sales_plan']['evidence']==[]
    assert 'benefit details unavailable' in record['message']
    render.assert_called_once()
    lock.release.assert_called_once()


def test_exact_listing_bullets_are_used_without_manual_benefit(tmp_path):
    payload=VideoRequest.model_construct(name='Orbit Desk Lamp',image_url='https://example.com/lamp.jpg',
                         product_url='https://seller.example/products/lamp',fact_sources=[
                         {'title':'Orbit Desk Lamp','url':'https://seller.example/products/lamp',
                          'snippet':'Adjustable arm for positioning the light.\nIncludes a weighted base.'}])
    record={'id':'e'*32,'status':'queued'}
    with patch('backend.photo_video_api.resolve_video_facts') as lookup, \
         patch('backend.photo_video_api._download_product_image',return_value=tmp_path/'photo.jpg'), \
         patch('backend.photo_video_api.render'), patch('backend.photo_video_api.lock'):
        worker(record,payload,tmp_path)
    lookup.assert_not_called()
    assert record['status']=='succeeded'
    assert record['sales_plan']['mode']=='benefit_led'
    assert 'weighted base' in record['sales_plan']['phrases'][2]

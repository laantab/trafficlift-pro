from unittest.mock import patch
import pytest
from fastapi import HTTPException
from backend.exact_product import amazon_asin,exact_listing,analyze_exact_product

URL='https://www.amazon.com/dp/B012345678'
HTML='<span id="productTitle">Chosen Juicer Model Z</span><input id="ASIN" value="B012345678"><img id="landingImage" src="https://example.com/juicer.jpg"><div id="feature-bullets">Includes a wide feed chute for fruit.</div>'

def test_short_link_token_is_not_product_identifier():
    assert amazon_asin('https://link.amazon/B02Yn5t0o') is None
    assert amazon_asin('https://amzn.to/B012345678') is None
    assert amazon_asin('https://amazon.com.evil.test/dp/B012345678') is None
    assert amazon_asin('https://www.amazon.com/dp/B012345678')=='B012345678'

@pytest.mark.parametrize('destination',['https://www.amazon.com/amazon-devices','https://www.amazon.com/hz/mobile/mission?foo=1','https://www.amazon.com/s?k=juicer'])
def test_department_shopping_search_pages_rejected(destination):
    with pytest.raises(ValueError):exact_listing('<meta property="og:title" content="Amazon Devices &amp; Accessories"><meta property="og:image" content="https://example.com/echo.jpg">',destination)

def test_main_product_title_and_photo_preserved():
    assert exact_listing(HTML,URL)==dict(name='Chosen Juicer Model Z',image_url='https://example.com/juicer.jpg',asin='B012345678',description='Includes a wide feed chute for fruit.')

def test_different_page_identifier_rejected():
    with pytest.raises(ValueError):exact_listing(HTML.replace('value="B012345678"','value="B098765432"'),URL)

def test_redirect_to_other_item_rejected_without_research():
    with patch('backend.exact_product._safe_get',return_value=('https://www.amazon.com/dp/B098765432',HTML,None)),patch('backend.live_research.research') as research:
        with pytest.raises(HTTPException) as exc:analyze_exact_product(URL)
    assert exc.value.status_code==422
    research.assert_not_called()

def test_unreadable_page_does_not_select_another_product():
    with patch('backend.exact_product._safe_get',return_value=(None,None,'timeout')),patch('backend.live_research.research') as research:
        with pytest.raises(HTTPException) as exc:analyze_exact_product(URL)
    assert exc.value.status_code==502
    research.assert_not_called()

def test_exact_response_no_general_search_or_photo_search():
    with patch('backend.exact_product._safe_get',return_value=(URL,HTML,None)),patch('backend.live_research.research') as research,patch('backend.product_control_agent.ProductControlAgent.evaluate') as evaluate:
        evaluate.side_effect=lambda winner:type('Report',(),{'ok':True,'product':winner})()
        result=analyze_exact_product('https://link.amazon/share-token')
    assert result['name']=='Chosen Juicer Model Z'
    assert result['image_url']=='https://example.com/juicer.jpg'
    assert result['url']==URL
    assert result['product_identity_verified'] is True
    assert result['trend_score']==0
    research.assert_not_called()

def test_matching_non_amazon_product_supported():
    html='<meta property="og:title" content="Brand Desk Lamp"><script type="application/ld+json">{"@type":"Product","name":"Brand Desk Lamp","image":"https://example.com/lamp.jpg"}</script>'
    assert exact_listing(html,'https://shop.example/products/lamp')['name']=='Brand Desk Lamp'

def test_recommended_product_block_cannot_replace_main_product():
    html=HTML+'<script type="application/ld+json">{"@type":"Product","name":"Other Blender","image":"https://example.com/blender.jpg"}</script>'
    assert exact_listing(html,URL)['image_url']=='https://example.com/juicer.jpg'


def test_amazon_mobile_product_paths_recognized():
    assert amazon_asin('https://www.amazon.com/gp/aw/d/B012345678?tag=demo-20') == 'B012345678'
    assert amazon_asin('https://www.amazon.com/Some-Name/dp/B012345678?tag=demo-20') == 'B012345678'
    assert amazon_asin('https://www.amazon.com/gp/product/B012345678') == 'B012345678'
    assert amazon_asin('https://www.amazon.com/s?k=organizer&tag=demo-20') is None


def test_mobile_amazon_product_link_auto_recovers_canonical_listing():
    mobile='https://www.amazon.com/gp/aw/d/B012345678?tag=demo-20'
    calls=[]
    def fetch(url, timeout=10):
        calls.append(url)
        if len(calls)==1:
            return 'https://www.amazon.com/s?k=organizer', '<title>Amazon shopping</title>', None
        return URL, HTML, None
    with patch('backend.exact_product._safe_get',side_effect=fetch),patch('backend.product_control_agent.ProductControlAgent.evaluate') as evaluate:
        evaluate.side_effect=lambda winner:type('Report',(),{'ok':True,'product':winner})()
        result=analyze_exact_product(mobile)
    assert len(calls)==2
    assert '/dp/B012345678' in calls[-1]
    assert result['affiliate_candidate_url']==mobile
    assert result['product_identity_verified'] is True
    assert result['name']=='Chosen Juicer Model Z'


def test_unidentified_amazon_department_link_cannot_guess_product():
    affiliate_home='https://www.amazon.com/?tag=demo-20'
    with patch('backend.exact_product._safe_get',return_value=(affiliate_home,'<title>Amazon shopping</title>',None)) as fetch:
        with pytest.raises(HTTPException) as exc:
            analyze_exact_product(affiliate_home)
    assert exc.value.status_code==422
    assert fetch.call_count==1

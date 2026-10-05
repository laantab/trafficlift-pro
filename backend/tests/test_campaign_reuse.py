from unittest.mock import patch
from fastapi.testclient import TestClient
import trafficlift_pro as server

def test_saved_campaign_reopens_without_scraping_or_generating():
    saved={'id':'saved-one','input_url':'https://seller.example/lamp','mode':'organic',
           'channels':['pinterest'],'budget':25,'product_title':'Orbit Lamp',
           'product_image':'https://seller.example/lamp.jpg','ai_mode':'template_fallback',
           'assets':{'pinterest_seo_engine':{'pin_title':'Original title','optimized_description':'Original saved copy'}}}
    with patch.object(server.db,'get_by_id',return_value=saved.copy()), \
         patch.object(server.generator,'generate') as generate:
        response=TestClient(server.app).get('/api/v1/campaigns/saved-one')
        assert response.status_code==200
        result=response.json()
        assert result['scraped_product']['title']=='Orbit Lamp'
        assert result['scraped_product']['primary_image']==saved['product_image']
        assert result['compiled_package']==saved['assets']
        assert result['campaign_id']=='saved-one'
        assert result['meta']['restored_from_history'] is True
        generate.assert_not_called()


def test_history_restores_exact_seller_facts_and_description():
    packet={'scraped_product':{'title':'Orbit Lamp','primary_image':'https://seller.example/lamp.jpg',
                             'description':'Adjustable arm for positioning the light.'},
            'video_facts':{'seller_benefit':'Adjustable arm for positioning the light.',
                          'seller_benefit_source':'https://seller.example/lamp',
                          'research_sources':[{'url':'https://seller.example/lamp','snippet':'Includes a weighted base.'}]}}
    saved={'id':'saved-two','input_url':'https://seller.example/lamp','assets':{'_product_source':packet,'pinterest_seo_engine':{}}}
    with patch.object(server.db,'get_by_id',return_value=saved):
        result=TestClient(server.app).get('/api/v1/campaigns/saved-two').json()
    assert result['scraped_product']==packet['scraped_product']
    assert result['video_facts']==packet['video_facts']
    assert '_product_source' not in result['compiled_package']

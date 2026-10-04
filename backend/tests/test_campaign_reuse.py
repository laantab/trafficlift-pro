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

from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from backend import discovery_engine as engine
from backend.live_research import ResearchEnvelope, _query_tavily
from backend.product_facts import facts_from_sources
from backend.photo_video import narration_starts, scene_geometry
from backend.sales_script import build_sales_plan


def test_nine_real_products_without_demand_terms_return_new_opportunity(tmp_path,monkeypatch):
    monkeypatch.setenv('TRAFFICLIFT_DISCOVERY_DB',str(tmp_path/'history.sqlite3'))
    sources=[dict(title=f'Orbit{i} Desk Lamp',snippet='Adjustable arm for positioning the light.',
                  url=f'https://seller.example/products/lamp-{i}',provider='test',
                  image_urls=[f'https://cdn.example/{i}.jpg']) for i in range(9)]
    env=ResearchEnvelope(research_status='live',research_timestamp='now',research_provider='test',
                         research_query='lamps',research_sources=sources)
    monkeypatch.setattr(engine.live_research,'research',lambda *a,**kw:env)
    monkeypatch.setattr('backend.listing_photo.listing_images',lambda *a,**kw:[])
    monkeypatch.setattr('backend.product_control_agent._validate_image',lambda *a,**kw:SimpleNamespace(ok=True))
    monkeypatch.setattr('backend.product_control_agent.ProductControlAgent.evaluate',lambda p:SimpleNamespace(ok=True,product=p))
    monkeypatch.setattr('backend.discovery._find_image_for_candidate',lambda *a,**kw:None)
    a=engine.discover(client_id='browser123456789',seed=4)
    b=engine.discover(client_id='browser123456789',seed=4)
    assert a['id']!=b['id']
    assert a['discovery']['candidates_attempted']==9
    assert a['demand_evidence_status']=='not_verified'
    assert 'not a proven seller' in a['trend_signals'][1]
    assert a['image_origin']=='image_attached_to_exact_listing'
    assert a['seller_benefit']=='Adjustable arm for positioning the light.'


def test_result_images_stay_attached_to_exact_listing(monkeypatch):
    monkeypatch.setattr('backend.live_research._is_tavily_configured',lambda:True)
    response=SimpleNamespace(status_code=200,json=lambda:{'results':[
        {'title':'Orbit Desk Lamp','url':'https://seller.example/products/lamp','content':'Adjustable arm for positioning the light.',
         'images':['https://cdn.example/1.jpg',{'url':'https://cdn.example/2.jpg'}]},
        {'title':'Atlas Travel Mug','url':'https://seller.example/products/mug','content':'Handle for holding your drink.','images':['https://cdn.example/mug.jpg']}],
         'images':['https://cdn.example/global-unrelated.jpg']})
    with patch('backend.live_research.requests.post',return_value=response):sources,global_images=_query_tavily('lamps',4)
    assert sources[0].image_urls==['https://cdn.example/1.jpg','https://cdn.example/2.jpg']
    facts=facts_from_sources('Orbit Desk Lamp','https://seller.example/products/lamp',[vars(s) for s in sources])
    assert facts['product_images']==sources[0].image_urls
    assert not facts_from_sources('Other product','https://seller.example/products/other',[vars(s) for s in sources])


def test_complete_script_always_has_five_stages_and_final_cta():
    plan=build_sales_plan('Orbit Desk Lamp','Adjustable arm for positioning the light','https://seller.example/products/lamp',60,
                          feature_details=['Includes a weighted base','Offers adjustable brightness'])
    assert len(plan['phrases'])==5
    assert 'weighted base' in plan['phrases'][2]
    assert 'adjustable brightness' in plan['phrases'][2]
    assert plan['scenes'][3]['stage']=='buying_details'
    assert plan['scenes'][4]['stage']=='cta'
    lengths=[3,8,6,5,8];seconds=34
    starts=narration_starts(lengths,seconds)
    assert len(starts)==5
    assert abs(seconds-(starts[-1]+lengths[-1])-1.2)<.001


def test_visible_motion_keeps_entire_photo_inside_panel():
    for size in [(888,888),(888,420),(420,888)]:
        for scene in range(5):
            first=scene_geometry(size,scene,0);last=scene_geometry(size,scene,1)
            assert abs(last[0]-first[0])>=int(size[0]*.26)
            for phase in [i/20 for i in range(21)]:
                w,h,x,y=scene_geometry(size,scene,phase)
                assert 96<=x and x+w<=984
                assert 510<=y and y+h<=1398


def test_quiet_launcher_only_reuses_owned_server():
    root=Path(__file__).parents[2]
    script=(root/'local_video/start_studio_quiet.ps1').read_text()
    assert '-WindowStyle Hidden -RedirectStandardOutput' in script
    assert "$server.CommandLine -notmatch 'trafficlift_pro:app'" in script
    assert '$ancestor.ExecutablePath -eq $python' in script
    assert 'Stop-Process' not in script
    assert 'Mutex' in script
    assert '--app=' in script
    assert 'shell.Run command, 0, False' in (root/'Start_TrafficLift.vbs').read_text()

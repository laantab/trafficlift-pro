import json
from backend import listing_photo

def page(monkeypatch, body, error=None):
    monkeypatch.setattr(listing_photo, '_safe_get', lambda *a, **k: ('https://shop.example/product',body,error))

def test_reads_exact_product_metadata_without_search_images(monkeypatch):
    page(monkeypatch, '<meta property="og:title" content="Levoit Core P350 Pet Air Purifier - Store"><meta property="og:image" content="/photo.jpg">')
    assert listing_photo.listing_images('Levoit Core P350 Pet Air Purifier','https://shop.example/product') == ['https://shop.example/photo.jpg']

def test_jsonld_graph_image_shapes_and_unrelated_products(monkeypatch):
    data={'@graph':[{'@type':'Product','name':'Other Product','image':'/wrong.jpg'},
                   {'@type':['Product'],'name':'Core P350 Purifier','image':[{'url':'/right.webp'},'/right.webp']} ]}
    page(monkeypatch, '<script type="application/ld+json">'+json.dumps(data)+'</script>')
    assert listing_photo.listing_images('Core P350 Purifier','https://shop.example/product')==['https://shop.example/right.webp']

def test_rejects_blocked_or_unrelated_pages(monkeypatch):
    page(monkeypatch,'<title>Access Denied</title><meta property="og:image" content="/logo.png">')
    assert not listing_photo.listing_images('Core P350 Purifier','https://shop.example/product')
    page(monkeypatch,'<title>Core P350 Purifier</title><meta property="og:image" content="/logo.png">','http_403')
    assert not listing_photo.listing_images('Core P350 Purifier','https://shop.example/product')


def test_search_retailer_suffix_does_not_break_exact_model_match(monkeypatch):
    data={'@type':'Product','name':'Levoit Core P350 Pet Air Purifier','image':'/right.jpg'}
    page(monkeypatch,'<script type="application/ld+json">'+json.dumps(data)+'</script>')
    assert listing_photo.listing_images('Levoit Core P350 Pet Air Purifier - Target','https://shop.example/product')==['https://shop.example/right.jpg']
    assert not listing_photo.listing_images('Levoit Core P400 Pet Air Purifier - Target','https://shop.example/product')


def test_store_roundups_and_guides_are_not_products():
    from backend.discovery import _looks_like_article
    for title in ['The 10 Best Light Fixture Stores for 2026 | Free Buyers Guide',
                  '10 Best Desk Lamps', 'Best Lighting Stores', "Lamp Buyer’s Guide", 'Where to Buy Lamps']:
        assert _looks_like_article(title,'')
    assert not _looks_like_article('Philips Hue White A19 LED Bulb 2-Pack','')
    assert not _looks_like_article('10 Piece Silicone Kitchen Utensil Set','')


def test_only_matching_product_supplies_seller_benefit(monkeypatch):
    data={'@type':'Product','name':'Orbit Desk Lamp','description':'<p>Adjustable arm for positioning the light.</p>','image':'/lamp.jpg'}
    page(monkeypatch,'<script type="application/ld+json">'+json.dumps(data)+'</script>')
    details={}
    listing_photo.listing_images('Orbit Desk Lamp','https://shop.example/product',details=details)
    assert details['seller_benefit']=='Adjustable arm for positioning the light.'
    assert details['seller_benefit_source']=='https://shop.example/product'
    details={}
    listing_photo.listing_images('Other Desk Lamp','https://shop.example/product',details=details)
    assert details=={}


def test_inline_emphasis_does_not_detach_qualification():
    from backend.listing_photo import usable_facts
    assert usable_facts('<p>Fits most holders, <b>except narrow ones.</b></p>')==['Fits most holders, except narrow ones.']
    assert usable_facts('<ul><li>Includes a weighted base.</li><li>Adjustable arm for positioning the light.</li></ul>')==['Includes a weighted base.','Adjustable arm for positioning the light.']

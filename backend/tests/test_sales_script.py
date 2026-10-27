import pytest
from backend.sales_script import build_sales_plan


def plan(**changes):
    data=dict(name='Stanley Quencher 30 oz', benefit='Handle and straw for sipping at your desk',
              destination='https://example.com/exact-model', seconds=30)
    data.update(changes)
    return build_sales_plan(**data)


def test_script_uses_product_fact_source_and_one_cta():
    result=plan(buyer_need='Easier sipping at your desk', consideration='Includes a straw')
    assert result['review']['status']=='PASS'
    assert result['phrases'][0]=='Easier sipping at your desk?'
    assert 'Handle and straw' in result['phrases'][1]
    assert 'Includes a straw' in result['phrases'][3]
    assert sum('Tap the product link' in x for x in result['phrases'])==1
    assert len(result['hook_candidates'])==3
    assert result['evidence'][0]['source_url']=='https://example.com/exact-model'
    assert 'not independently verified' in result['review']['evidence_status']
    assert all('performance test' in scene['visual'] or scene['stage']!='benefit' for scene in result['scenes'])


def test_missing_benefit_produces_honest_preview_with_cta():
    result=plan(benefit='',buyer_need='Guaranteed weight loss',consideration='Includes a straw',seconds=15)
    assert result['mode']=='product_overview'
    assert result['review']['status']=='PASS'
    assert result['evidence']==[]
    script=' '.join(result['phrases'])
    assert 'Guaranteed' not in script and 'straw' not in script
    assert 'current price and options' in script
    assert len(result['phrases'])==5
    assert 'benefit details unavailable' in result['review']['evidence_status']


def test_preview_fits_fifteen_seconds_with_long_product_name():
    result=plan(name=' '.join(['Product']*11),benefit='',seconds=15)
    assert result['review']['status']=='PASS'
    assert result['product']==' '.join(['Product']*11)


@pytest.mark.parametrize('benefit',['Guaranteed results','Hurry, selling out','We tested it','Only $19.99','Save 50% off'])
def test_rejects_unsubstantiated_sales_or_price_language(benefit):
    with pytest.raises(ValueError):plan(benefit=benefit)


def test_duration_review_does_not_truncate_the_fact():
    fact=' '.join(['fact']*22)
    with pytest.raises(ValueError,match='longer video'):plan(benefit=fact,seconds=15)
    assert fact in plan(benefit=fact,seconds=60)['phrases'][1]


def test_fact_is_not_replaced_by_invented_performance():
    result=plan()
    text=' '.join(result['phrases']).lower()
    assert 'leak' not in text and 'cold for' not in text and 'cup holder' not in text


def test_seller_markdown_and_numeric_references_not_spoken_or_captioned():
    from backend.sales_script import clean
    assert clean('### Warranty & Support [12]') == 'Warranty & Support'
    assert clean('**Clear organizers** [2, 3]') == 'Clear organizers'
    assert clean('[Compare options](https://example.com/listing)') == 'Compare options'
    assert clean('- Includes three organizers') == 'Includes three organizers'


def test_seller_html_or_code_must_not_become_ad_copy():
    from backend.sales_script import clean
    import pytest
    with pytest.raises(ValueError):
        clean('<script>Example</script>')
    with pytest.raises(ValueError):
        clean('broken {template} value')


def test_bare_retailer_urls_and_truncated_snippets_never_reach_narration():
    from backend.sales_script import clean
    for raw in ('Adjustable arm with 360 degree swivel https://seller.example/product',
                'Includes a weighted base...',
                'Adjustable arm with 360 degree swivel | see details'):
        with pytest.raises(ValueError, match='plain-text|formatting'):
            clean(raw)


def test_night_light_overview_is_product_specific_without_invented_features():
    result=build_sales_plan(
        'JAS11242 LED Motion-Sensor Night Light Nickel Finish', '',
        'https://seller.example/jas11242', 30)
    assert result['creative_review']['status']=='PASS'
    assert result['product'].startswith('JAS11242')
    assert all('Picture your setup' not in phrase for phrase in result['phrases'])
    assert all('Review listed features' not in phrase for phrase in result['phrases'])
    assert all('Key item features' not in caption for caption in result['captions'])
    assert 'JAS11242' in ' '.join(result['captions'])
    assert 'JAS11242' in ' '.join(result['phrases'])
    assert result['evidence']==[]


@pytest.mark.parametrize('placeholder', [
    'Review listed features', 'Picture your setup', 'Key item features',
    'Explore Tech & Gadgets Ideas', 'Explore Useful Product Ideas',
])
def test_generic_creative_placeholders_are_rejected(placeholder):
    from backend.sales_script import creative_copy_gate
    plan = {'product':'JAS11242 Motion-Sensor Night Light',
            'phrases':['Considering JAS11242 Motion-Sensor Night Light?',
                       'Here is the exact JAS11242 Motion-Sensor Night Light.',
                       'Check the exact seller listing.'],
            'captions':['JAS11242 Motion-Sensor Night Light', placeholder, 'Check seller listing']}
    with pytest.raises(ValueError, match='Generic placeholder'):
        creative_copy_gate(plan)


def test_creative_copy_must_identify_exact_product_on_screen_and_in_voice():
    from backend.sales_script import creative_copy_gate
    plan = {'product':'JAS11242 Motion-Sensor Night Light',
            'phrases':['Considering a night light?', 'Compare its size.', 'See the listing.'],
            'captions':['JAS11242 Motion-Sensor Night Light', 'Exact product', 'Listing']}
    with pytest.raises(ValueError, match='Narration'):
        creative_copy_gate(plan)
    plan['phrases'][0]='Considering JAS11242 Motion-Sensor Night Light?'
    plan['captions']=['Lighting ideas','Check details','See listing']
    with pytest.raises(ValueError, match='captions'):
        creative_copy_gate(plan)


def test_jas11242_photo_only_overview_is_not_approved_for_marketing_render():
    from backend.sales_script import marketing_readiness_gate
    overview=build_sales_plan('JAS11242 LED Motion-Sensor Night Light', '',
                              'https://seller.example/jas11242', 30)
    assert overview['mode']=='product_overview'
    with pytest.raises(ValueError,match='Not enough product-specific seller facts'):
        marketing_readiness_gate(overview)


def test_marketing_render_requires_two_distinct_exact_product_facts():
    from backend.sales_script import marketing_readiness_gate
    single=build_sales_plan('JAS11242 LED Motion-Sensor Night Light',
                            'Nickel finish', 'https://seller.example/jas11242', 30)
    with pytest.raises(ValueError,match='two distinct product facts'):
        marketing_readiness_gate(single)
    grounded=build_sales_plan('JAS11242 LED Motion-Sensor Night Light',
                              'Nickel finish', 'https://seller.example/jas11242', 30,
                              feature_details=['Motion sensor is listed on the exact product page'])
    assert marketing_readiness_gate(grounded)['distinct_product_facts']==2

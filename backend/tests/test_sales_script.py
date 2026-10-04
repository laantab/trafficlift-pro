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
    assert 'Includes a straw' in result['phrases'][1]
    assert sum('Tap the product link' in x for x in result['phrases'])==1
    assert len(result['hook_candidates'])==3
    assert result['evidence'][0]['source_url']=='https://example.com/exact-model'
    assert 'not independently verified' in result['review']['evidence_status']
    assert all('performance test' in scene['visual'] or scene['stage']!='benefit' for scene in result['scenes'])


def test_missing_benefit_produces_honest_preview_with_cta():
    result=plan(benefit='',buyer_need='Guaranteed weight loss',consideration='Includes a straw',seconds=15)
    assert result['mode']=='photo_preview'
    assert result['review']['status']=='PASS'
    assert result['evidence']==[]
    script=' '.join(result['phrases'])
    assert 'Guaranteed' not in script and 'straw' not in script
    assert 'current pricing and specifications' in script
    assert 'no benefit claims' in result['review']['evidence_status']


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

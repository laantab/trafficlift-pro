from dataclasses import asdict
import json
from backend.generate import TrafficLiftGenerator
from backend.scrape import ScrapedProduct

def test_listing_based_assets_do_not_invent_reviews_or_scarcity(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    product = ScrapedProduct(url='https://example.com/product',
        title='Amazon.com: Cold Press Juicer, Slow Single Auger Juicer : Home & Kitchen',
        description='Wide feed chute and reverse function.',raw_keywords=['amazoncom','cold','press','juicer'])
    package=TrafficLiftGenerator().generate(product,'organic',['pinterest','tiktok_organic','youtube_shorts','twitter_threads'])
    text=json.dumps(asdict(package)).lower()
    for invented in ['limited stock','thousands','i have been using','not a sponsored','top-rated','exclusive access','before it is gone','amazoncom']:
        assert invented not in text
    assert 'wide feed chute' in text
    assert package.pinterest_seo_engine.board_title=='Juicers Buying Guide'
    assert all(len(tweet)<=260 for tweet in package.twitter_viral_thread.tweets)

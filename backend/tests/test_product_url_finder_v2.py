from unittest.mock import patch

import pytest

from backend.product_url_finder_v2 import (
    FetchSnapshot,
    resolve_product_identity,
)


def test_short_link_uses_exact_asin_seen_in_redirect_chain():
    share = "https://link.amazon/B03bHi8EM"
    generic = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?x=1",
        html="<html><head><title>Amazon Shopping</title></head></html>",
        status_code=200,
        history_urls=["https://www.amazon.com/dp/B012345678"],
    )
    product = FetchSnapshot(
        final_url="https://www.amazon.com/dp/B012345678",
        html=(
            '<span id="productTitle">Silicone Baking Mat</span>'
            '<img id="landingImage" '
            'data-old-hires="https://m.media-amazon.com/images/I/mat-main.jpg">'
            '<input id="ASIN" value="B012345678">'
        ),
        status_code=200,
        history_urls=[],
    )
    with patch(
        "backend.product_url_finder_v2._browser_get",
        side_effect=[generic, product],
    ):
        result = resolve_product_identity(share)

    assert result["asin"] == "B012345678"
    assert result["name"] == "Silicone Baking Mat"
    assert result["image_url"] == "https://m.media-amazon.com/images/I/mat-main.jpg"
    assert result["source"] == "amazon_browser_exact"


def test_short_link_accepts_product_specific_metadata_from_same_response():
    share = "https://link.amazon/B03bHi8EM"
    mission = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=opaque",
        html=(
            '<meta property="og:title" content="Silicone Baking Mat">'
            '<meta property="og:image" '
            'content="https://m.media-amazon.com/images/I/mat-main.jpg">'
            '<meta name="description" '
            'content="Reusable nonstick baking surface for cookies and roasting.">'
        ),
        status_code=200,
        history_urls=[],
    )
    with patch(
        "backend.product_url_finder_v2._browser_get",
        return_value=mission,
    ):
        result = resolve_product_identity(share)

    assert result["name"] == "Silicone Baking Mat"
    assert result["image_url"] == "https://m.media-amazon.com/images/I/mat-main.jpg"
    assert result["source"] == "amazon_share_metadata"


def test_generic_amazon_shopping_page_is_not_accepted_as_product():
    share = "https://link.amazon/B03bHi8EM"
    generic = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=opaque",
        html=(
            '<meta property="og:title" content="Amazon.com: Online Shopping">'
            '<meta property="og:image" '
            'content="https://images-na.ssl-images-amazon.com/images/G/01/gno/amazon-logo.png">'
        ),
        status_code=200,
        history_urls=[],
    )
    with patch(
        "backend.product_url_finder_v2._browser_get",
        return_value=generic,
    ), patch(
        "backend.product_url_finder_v2._pin_factory_fallback",
        return_value=None,
    ):
        with pytest.raises(ValueError):
            resolve_product_identity(share)


def test_short_link_uses_first_previously_viewed_product_from_mission_page():
    share = "https://link.amazon/B03bHi8EM"
    mission_html = """
    <html><body>
      <div id="previously-viewed-content">
        <div>
          <a href="/Silicone-Resistant-Reusable-Sourdough-Pastries/dp/B0HJ7Z522R/ref=x">
            <img alt="Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet"
                 src="https://m.media-amazon.com/images/I/41oiCTE5kfL._MCnd_AC_.jpg">
          </a>
          <a href="/Silicone-Resistant-Reusable-Sourdough-Pastries/dp/B0HJ7Z522R/ref=title">
            Generic Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet
          </a>
        </div>
        <div>
          <a href="/Other-Recommendation/dp/B012345678/ref=y">
            <img alt="Other Product" src="https://m.media-amazon.com/images/I/other.jpg">
          </a>
        </div>
      </div>
    </body></html>
    """
    mission = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=opaque",
        html=mission_html,
        status_code=200,
        history_urls=[],
    )
    generic_canonical = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=still-generic",
        html="<title>Amazon Shopping</title>",
        status_code=200,
        history_urls=[],
    )
    with patch(
        "backend.product_url_finder_v2._browser_get",
        side_effect=[mission, generic_canonical],
    ), patch(
        "backend.product_url_finder_v2._pin_factory_fallback",
        return_value=None,
    ):
        result = resolve_product_identity(share)

    assert result["asin"] == "B0HJ7Z522R"
    assert result["name"].startswith("Generic Silicone Baking Mat")
    assert result["image_url"] == (
        "https://m.media-amazon.com/images/I/41oiCTE5kfL._AC_SL1500_.jpg"
    )
    assert result["source"] == "amazon_mission_previously_viewed"
    assert result["final_url"] == "https://www.amazon.com/dp/B0HJ7Z522R"


def test_short_link_uses_javascript_render_when_static_mission_shell_has_no_product():
    share = "https://link.amazon/B03bHi8EM"
    static_shell = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=opaque",
        html=(
            '<html><head><title>Amazon.com: Keep shopping for</title></head>'
            '<body><div id="previously-viewed-content"></div></body></html>'
        ),
        status_code=200,
        history_urls=[],
    )
    rendered_mission = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=opaque",
        html="""
        <html><body>
          <div id="previously-viewed-content">
            <div>
              <a href="/Silicone-Resistant-Reusable-Sourdough-Pastries/dp/B0HJ7Z522R/ref=x">
                <img alt="Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet"
                     src="https://m.media-amazon.com/images/I/41oiCTE5kfL._MCnd_AC_.jpg">
              </a>
              <a href="/Silicone-Resistant-Reusable-Sourdough-Pastries/dp/B0HJ7Z522R/ref=title">
                Generic Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet
              </a>
            </div>
          </div>
        </body></html>
        """,
        status_code=200,
        history_urls=[],
    )
    generic_canonical = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=still-generic",
        html="<title>Amazon Shopping</title>",
        status_code=200,
        history_urls=[],
    )

    with patch(
        "backend.product_url_finder_v2._browser_get",
        side_effect=[static_shell, generic_canonical],
    ), patch(
        "backend.product_url_finder_v2._javascript_get",
        return_value=rendered_mission,
    ), patch(
        "backend.product_url_finder_v2._pin_factory_fallback",
        return_value=None,
    ):
        result = resolve_product_identity(share)

    assert result["asin"] == "B0HJ7Z522R"
    assert "Silicone Baking Mat" in result["name"]
    assert result["image_url"] == (
        "https://m.media-amazon.com/images/I/41oiCTE5kfL._AC_SL1500_.jpg"
    )
    assert result["source"] == "amazon_mission_js_previously_viewed"


def test_rendered_mission_can_resolve_first_product_from_data_asin_without_dp_href():
    share = "https://link.amazon/B03bHi8EM"
    static_blocked = FetchSnapshot(
        final_url=share,
        html="<title>Service unavailable</title>",
        status_code=503,
        history_urls=[],
    )
    rendered_mission = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=opaque",
        html="""
        <html><body>
          <div id="mission-page-label">Baking mats</div>
          <div id="previously-viewed-content">
            <div class="card" data-asin="B0HJ7Z522R">
              <div class="image-wrap">
                <img alt="Generic Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet"
                     src="https://m.media-amazon.com/images/I/41oiCTE5kfL._MCnd_AC_.jpg">
              </div>
              <div class="title">Generic Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet</div>
            </div>
            <div class="card" data-asin="B012345678">
              <img alt="Other product" src="https://m.media-amazon.com/images/I/other.jpg">
            </div>
          </div>
        </body></html>
        """,
        status_code=200,
        history_urls=[],
    )

    with patch(
        "backend.product_url_finder_v2._browser_get",
        return_value=static_blocked,
    ), patch(
        "backend.product_url_finder_v2._javascript_get",
        return_value=rendered_mission,
    ), patch(
        "backend.product_url_finder_v2._pin_factory_fallback",
        return_value=None,
    ):
        result = resolve_product_identity(share)

    assert result["asin"] == "B0HJ7Z522R"
    assert "Silicone Baking Mat" in result["name"]
    assert result["image_url"] == (
        "https://m.media-amazon.com/images/I/41oiCTE5kfL._AC_SL1500_.jpg"
    )
    assert result["source"] == "amazon_mission_js_previously_viewed"


def test_rendered_mission_finds_real_previously_viewed_carousel_by_heading():
    share = "https://link.amazon/B03bHi8EM"
    static_shell = FetchSnapshot(
        final_url=share,
        html="<title>Amazon shell</title>",
        status_code=503,
        history_urls=[],
    )
    rendered_mission = FetchSnapshot(
        final_url="https://www.amazon.com/hz/mobile/mission?p=opaque",
        html="""
        <html><body>
          <div id="previously-viewed-content"></div>
          <section class="actual-carousel">
            <div class="heading"><h4>Previously viewed</h4></div>
            <div class="row">
              <div class="card">
                <a href="/Silicone-Resistant-Reusable-Sourdough-Pastries/dp/B0HJ7Z522R/ref=x">
                  <img alt="Generic Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet"
                       src="https://m.media-amazon.com/images/I/41oiCTE5kfL._MCnd_AC_.jpg">
                </a>
                <a href="/Silicone-Resistant-Reusable-Sourdough-Pastries/dp/B0HJ7Z522R/ref=title">
                  Generic Silicone Baking Mat, Food Grade Non Stick Heat Resistant Pastry Sheet
                </a>
              </div>
              <div class="card">
                <a href="/Other/dp/B012345678/ref=y">
                  <img alt="Other product" src="https://m.media-amazon.com/images/I/other.jpg">
                </a>
              </div>
            </div>
          </section>
        </body></html>
        """,
        status_code=200,
        history_urls=[],
    )

    with patch(
        "backend.product_url_finder_v2._browser_get",
        return_value=static_shell,
    ), patch(
        "backend.product_url_finder_v2._javascript_get",
        return_value=rendered_mission,
    ), patch(
        "backend.product_url_finder_v2._pin_factory_fallback",
        return_value=None,
    ):
        result = resolve_product_identity(share)

    assert result["asin"] == "B0HJ7Z522R"
    assert "Silicone Baking Mat" in result["name"]
    assert result["source"] == "amazon_mission_js_previously_viewed"

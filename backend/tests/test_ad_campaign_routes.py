from starlette.routing import Match
import trafficlift_pro


def test_video_campaign_status_reaches_its_owner_protected_handler():
    path = "/api/v1/ad-campaigns/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    scope = {"type": "http", "method": "GET", "path": path, "root_path": ""}
    first = next(route for route in trafficlift_pro.app.routes if route.matches(scope)[0] == Match.FULL)
    assert first.endpoint.__module__ == "backend.campaign_api"
    assert first.endpoint.__name__ == "status"

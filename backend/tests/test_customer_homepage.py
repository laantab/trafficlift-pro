"""A customer visiting the website must see the marketing dashboard, not Swagger."""
from fastapi.testclient import TestClient
from trafficlift_pro import app


def test_customer_homepage_opens_trafficlift_dashboard():
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Find Winning Product" in response.text
    assert "swagger-ui" not in response.text.lower()


def test_developer_docs_remain_accessible_separately():
    response = TestClient(app).get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text.lower()


def test_release_version_is_consistent_in_ui_and_backend():
    from trafficlift_pro import APP_VERSION
    assert APP_VERSION == "3.0.2"
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.text.count("v3.0.2") >= 3
    assert 'name="app-version" content="3.0.2"' in response.text
    assert "v3.0.1" not in response.text

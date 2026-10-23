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

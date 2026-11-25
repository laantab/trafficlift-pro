from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import ad_results as results


def test_source_scope_dedup_and_sync(tmp_path,monkeypatch):
    app=FastAPI()
    app.include_router(results.router)
    monkeypatch.setenv("TRAFFICLIFT_FREE_VIDEO","1")
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR",str(tmp_path))
    client=TestClient(app,base_url="http://localhost")
    base="/api/v1/ad-results"
    assert client.get("/analytics").is_success
    assert client.get(base).json()["reports"]==[]
    subject=dict(platform="Amazon",scope="Account",label="Store",reference="test-store")
    key=client.post(base+"/subjects",json=subject).json()["id"]
    route=f"{base}/{key}/report"
    row=dict(start="2026-01-01",end="2026-01-31",source="Associates",clicks=114,shipped_items=0)
    assert client.post(route,json=row).is_success
    assert client.post(route,json=row).is_success
    reports=client.get(base).json()["reports"]
    assert len(reports)==1 and reports[0]["counts"]=={"clicks":114,"shipped_items":0}
    assert reports[0]["ctr"] is None and "campaign-specific" in reports[0]["test"]
    from backend import youtube_publisher as yt
    monkeypatch.setattr(yt,"configured",lambda:True)
    monkeypatch.setattr(yt,"credentials",lambda: {})
    monkeypatch.setattr(yt,"api_get",lambda *a,**kw: {"items":[{"statistics":{"viewCount":"25","likeCount":"0"}}]})
    ytkey=next(s["id"] for s in client.get(base).json()["subjects"] if s["platform"]=="YouTube")
    assert client.post(f"{base}/{ytkey}/sync").is_success
    assert client.get(base).json()["reports"][0]["counts"]=={"views":25,"likes":0}
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO","1")
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN","s"*40)
    assert client.get(base).status_code==401

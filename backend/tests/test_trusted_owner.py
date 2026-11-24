from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.photo_video_api import router
from backend.owner_device import COOKIE


def test_return_and_forget_trusted_device(tmp_path,monkeypatch):
    origin="https://studio.example"
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO","1")
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR",str(tmp_path))
    monkeypatch.setenv("TRAFFICLIFT_HOSTED_VIDEO_ACCESS_TOKEN","a"*40)
    app=FastAPI()
    app.include_router(router)
    with TestClient(app,base_url=origin,headers={"origin":origin}) as c:
        enter="/api/v1/owner-device/enter"
        assert c.post(enter).status_code==401
        assert c.post("/api/v1/video-owner-login",json={"access_code":"a"*40,"remember_device":True}).is_success
        token=c.cookies.get(COOKIE)
        c.cookies.delete("trafficlift_video_session")
        assert c.post(enter,headers={"origin":"https://other.example"}).status_code==403
        assert c.post(enter).is_success
        assert c.cookies.get("trafficlift_video_session")
        assert c.post("/api/v1/owner-device/forget").is_success
        assert not c.cookies.get("trafficlift_video_session")
        c.cookies.set(COOKIE,token)
        assert c.post(enter).status_code==401

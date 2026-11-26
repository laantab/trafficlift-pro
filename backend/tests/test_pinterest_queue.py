import zipfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend import campaign_api, campaign_editor, pinterest_queue as pins


def test_schedule_publish_and_track_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAFFICLIFT_VIDEO_DIR", str(tmp_path))
    campaign = uuid4()
    folder = campaign_api.root()/campaign.hex
    folder.mkdir()
    (folder/"video.mp4").write_bytes(b"approved video")
    with zipfile.ZipFile(folder/"campaign.zip","w") as archive:
        archive.writestr("cover-frame.jpg", b"approved cover")
        archive.writestr("facebook-reels.txt", "A memory for Christmas.\n"+campaign_api.HOLIDAY["destination"])
    campaign_editor.receipt(folder, 10, 4)
    with campaign_api.database() as db:
        db.execute("INSERT INTO jobs VALUES(?,?,?,?)",(campaign.hex,"holiday",0,"ready"))
    created = []
    def fake_api(method,path,**kwargs):
        if path=="boards": return {"items":[{"id":"123","name":"Books"}]}
        if path=="media": return {"media_id":"8","upload_url":"https://example.org/upload","upload_parameters":{}}
        if path=="media/8": return {"status":"succeeded"}
        assert path=="pins"
        created.append(kwargs["json"])
        return {"id":"999"}
    monkeypatch.setattr(pins,"api",fake_api)
    class Success:
        def raise_for_status(self): pass
    monkeypatch.setattr(pins.requests,"post",lambda *a,**k: Success())
    selection=pins.Schedule(board="123",when=datetime.now(timezone.utc)+timedelta(hours=1))
    for _ in range(2): assert len(pins.schedule(campaign,selection))==1
    pins.tick()
    assert not created
    with pins.database() as db: db.execute("UPDATE pins SET due=0")
    pins.tick()
    pins.tick()
    assert len(created)==1 and created[0]["link"]==campaign_api.HOLIDAY["destination"]
    assert pins.history(campaign)[0]["state"]=="published and tracked"
    assert any(s["reference"].endswith("/999/") for s in pins.ad_results.overview()["subjects"])

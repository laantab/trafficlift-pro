"""Owner-only advertising measurements, separated by source, scope and reporting period."""
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.photo_video_api import video_guard, storage
from fastapi import Request


def owner(request: Request):
    video_guard(request)


router = APIRouter()
private = APIRouter(prefix="/api/v1/ad-results", dependencies=[Depends(owner)])
Platform = Literal["YouTube","TikTok","Instagram","Facebook","LinkedIn","Amazon","Payhip"]
COUNT = dict(default=None, ge=0, le=10**12)
FIELDS = ("impressions","views","clicks","orders","shipped_items","likes","comments","shares","sales_usd","commission_usd","spend_usd")


class Subject(BaseModel):
    platform: Platform
    scope: Literal["Post","Campaign","Account"]
    label: str = Field(min_length=1,max_length=150)
    reference: str = Field(min_length=1,max_length=500)
    creative: str = Field(default="",max_length=200)


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=2,max_length=150)
    start: date
    end: date
    impressions: int | None = Field(**COUNT)
    views: int | None = Field(**COUNT)
    clicks: int | None = Field(**COUNT)
    orders: int | None = Field(**COUNT)
    shipped_items: int | None = Field(**COUNT)
    sales_usd: float | None = Field(default=None,ge=0,le=10**12,allow_inf_nan=False)
    commission_usd: float | None = Field(default=None,ge=0,le=10**12,allow_inf_nan=False)
    spend_usd: float | None = Field(default=None,ge=0,le=10**12,allow_inf_nan=False)
    likes: int | None = Field(**COUNT)
    comments: int | None = Field(**COUNT)
    shares: int | None = Field(**COUNT)

    @model_validator(mode="after")
    def valid_period(self):
        if self.end < self.start or self.end > datetime.now(timezone.utc).date():
            raise ValueError("Check the report dates.")
        if not any(getattr(self,f) is not None for f in FIELDS):
            raise ValueError("Enter at least one reported count.")
        return self


def database():
    folder=storage()/"analytics"
    folder.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(folder/"performance.sqlite",timeout=10)
    conn.row_factory=sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS subjects(id INTEGER PRIMARY KEY,platform TEXT,scope TEXT,label TEXT,
      reference TEXT,creative TEXT,UNIQUE(platform,scope,reference));
    CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY,subject INTEGER,source TEXT,kind TEXT,
      start TEXT,end TEXT,counts TEXT,recorded TEXT, UNIQUE(subject,source,kind,start,end));
    """)
    # Known published posts supply identity only. All counts come from reports.
    conn.executemany("INSERT OR IGNORE INTO subjects(platform,scope,label,reference,creative) VALUES(?,?,?,?,?)", [
        ("YouTube","Post","A Season for Everyone","https://www.youtube.com/shorts/BcxC16-eWyw","Christmas paperback Short"),
        ("Facebook","Post","A Season for Everyone","https://www.facebook.com/122166192195121238/posts/122166192171121238","Whose laugh comes to mind? Feed post"),
    ])
    conn.commit()
    return conn


def add_subject(db, row):
    values=[getattr(row,k).strip() for k in ("platform","scope","label","reference","creative")]
    if not values[2] or not values[3]:
        raise HTTPException(422,"Enter the campaign name and post link or report identifier.")
    db.execute("INSERT OR IGNORE INTO subjects(platform,scope,label,reference,creative) VALUES(?,?,?,?,?)",values)
    return db.execute("SELECT id FROM subjects WHERE platform=? AND scope=? AND reference=?",
                      (values[0],values[1],values[3])).fetchone()[0]


def target(db, key):
    row=db.execute("SELECT * FROM subjects WHERE id=?",(key,)).fetchone()
    if row is None:
        raise HTTPException(404,"Choose a saved post or campaign.")
    return row


def save(db,key,source,kind,start,end,counts):
    db.execute("""INSERT INTO reports(subject,source,kind,start,end,counts,recorded) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(subject,source,kind,start,end) DO UPDATE SET counts=excluded.counts,recorded=excluded.recorded""",
        (key,source,kind,start,end,json.dumps(counts),datetime.now(timezone.utc).isoformat()))


def analysis(counts,scope):
    clicks=counts.get("clicks")
    impressions=counts.get("impressions")
    views=counts.get("views")
    orders=counts.get("orders")
    ctr=round(100*clicks/impressions,2) if impressions and clicks is not None else None
    if scope=="Account":
        test="Use a campaign-specific report or tracking ID before crediting an ad with these results."
    elif clicks and orders==0:
        test="Check the purchase link and offer. Test one change to the product page and compare the next matching period."
    elif clicks==0 and (views or impressions):
        test="Test a clearer purchase invitation and link placement. Keep the opening the same."
    elif clicks is None and views:
        test="Views show exposure. Add outbound clicks for this post and period to evaluate the purchase invitation."
    elif orders and clicks:
        test="Repeat this creative with one different opening and compare clicks and attributed orders over equal periods."
    else:
        test="Collect views, outbound clicks and attributed orders for the same post and reporting period."
    spend=counts.get("spend_usd")
    sales=counts.get("sales_usd")
    roas=round(sales/spend,2) if spend and sales is not None else None
    return {"ctr":ctr,"roas":roas,"test":test}


@router.get("/analytics")
def page():
    return FileResponse(Path(__file__).resolve().parents[1]/"static"/"analytics.html",headers={"Cache-Control":"no-store"})


@private.get("")
def overview():
    with closing(database()) as db, db:
        subjects=[dict(r) for r in db.execute("SELECT * FROM subjects ORDER BY id DESC")]
        reports=[]
        for row in db.execute("SELECT r.*,s.scope,s.platform,s.label,s.creative FROM reports r JOIN subjects s ON s.id=r.subject ORDER BY end DESC,recorded DESC"):
            record=dict(row)
            record["counts"]=json.loads(record["counts"])
            reports.append({**record,**analysis(record["counts"],record["scope"])})
        return {"subjects":subjects,"reports":reports}


@private.post("/subjects")
def register(row: Subject):
    with closing(database()) as db, db:
        return {"id":add_subject(db,row)}


@private.post("/{key}/report")
def record(key:int,row:Measurement):
    with closing(database()) as db, db:
        target(db,key)
        save(db,key,row.source.strip(),"Reported period",row.start.isoformat(),row.end.isoformat(),
             {f:getattr(row,f) for f in FIELDS if getattr(row,f) is not None})
    return {"saved":True}


def video_id(row):
    url=urlsplit(row["reference"])
    host=(url.hostname or "").lower()
    path=url.path.strip("/").split("/")
    if row["scope"]=="Post" and row["platform"]=="YouTube" and host in {"youtube.com","www.youtube.com","m.youtube.com","youtu.be"}:
        ident=(path[0] if host=="youtu.be" else path[-1] if path[0]=="shorts" else parse_qs(url.query).get("v",[""])[0])
        if re.fullmatch(r"[A-Za-z0-9_-]{11}",ident):
            return ident
    if row["scope"]=="Post" and row["platform"]=="TikTok" and host in {"www.tiktok.com","tiktok.com"} and len(path)>=3 and path[-2]=="video" and re.fullmatch(r"[0-9]{10,24}",path[-1]):
        return path[-1]
    raise HTTPException(422,"Sync needs the full YouTube or TikTok post link. For other reports, enter the platform counts.")


@private.post("/{key}/sync")
def sync(key:int):
    with closing(database()) as db:
        row=target(db,key)
    ident=video_id(row)
    if row["platform"]=="YouTube":
        from backend import youtube_publisher as yt
        if not yt.configured():
            raise HTTPException(409,"Connect YouTube in Launch Studio, or enter its report below.")
        response=yt.api_get("videos",yt.credentials(),part="statistics",id=ident)
        items=response.get("items",[])
        values=items[0].get("statistics",{}) if items else {}
        mapping={"viewCount":"views","likeCount":"likes","commentCount":"comments"}
    else:
        token=os.getenv("TRAFFICLIFT_TIKTOK_READ_TOKEN")
        if not token:
            raise HTTPException(409,"TikTok read access is not connected. Enter the post’s TikTok Studio counts below.")
        try:
            resp=requests.post("https://open.tiktokapis.com/v2/video/query/",
                params={"fields":"id,view_count,like_count,comment_count,share_count"},
                headers={"Authorization":"Bearer "+token},json={"filters":{"video_ids":[ident]}},timeout=15,allow_redirects=False)
            resp.raise_for_status()
            items=resp.json().get("data",{}).get("videos",[])
        except (requests.RequestException,ValueError,AttributeError) as exc:
            raise HTTPException(502,"TikTok could not return this post’s statistics.") from exc
        values=next((v for v in items if str(v.get("id"))==ident),{})
        mapping={"view_count":"views","like_count":"likes","comment_count":"comments","share_count":"shares"}
    counts={out:int(values[k]) for k,out in mapping.items() if str(values.get(k,"")).isdigit()}
    if not counts:
        raise HTTPException(409,"No counts were returned for this post. Check the link and account access.")
    with closing(database()) as db, db:
        save(db,key,row["platform"]+" API","Lifetime snapshot","",datetime.now(timezone.utc).date().isoformat(),counts)
    return {"saved":True}


@private.post("/refresh")
def refresh_report():
    with closing(database()) as db:
        subjects = list(db.execute("SELECT * FROM subjects ORDER BY id"))
    statuses = []
    for row in subjects:
        status = "Uses the latest saved platform report"
        if row["scope"] == "Post" and row["platform"] in {"TikTok", "YouTube"}:
            try:
                sync(row["id"])
                status = "Updated from platform"
            except HTTPException as exc:
                status = exc.detail if exc.status_code in (409, 422) else "Platform refresh unavailable"
            except Exception:
                status = "Platform refresh unavailable"
        statuses.append({"subject": row["id"], "status": status})
    return {"statuses": statuses}


router.include_router(private)

import base64
from contextlib import closing
import io
import json
import logging
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl
from backend import photo_video_api as studio
from backend.campaign_editor import EditorialHold, verified
from backend.campaigns import ASSETS, HOLIDAY, plan, render_campaign

router = APIRouter()
render_lock = threading.Lock()
log = logging.getLogger(__name__)


def root():
    folder = studio.storage() / "campaigns"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def database():
    db = sqlite3.connect(root() / "campaigns.sqlite")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, product TEXT, variant INTEGER, status TEXT)")
    return db


class Product(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    destination: HttpUrl
    feature: str = Field(min_length=3, max_length=120)
    prompt: str = Field(default="", max_length=100)
    affiliate: bool = False
    interior_pdf: str = Field(default="", max_length=50_000_000)
    images: list[str] = Field(min_length=1, max_length=3)


class Campaign(BaseModel):
    product: str = Field(max_length=40)
    request_id: uuid.UUID


@router.get("/owner")
@router.get("/campaigns")
def page():
    return FileResponse(ASSETS.parents[1] / "static" / "campaigns.html")


@router.get("/api/v1/campaign-products")
def products(request: Request):
    studio.video_guard(request)
    with database() as db:
        return [{"id":"holiday", "name":HOLIDAY["name"]}] + [
            {"id":row["id"], "name":json.loads(row["data"])["name"]} for row in db.execute("SELECT * FROM products")]


@router.post("/api/v1/campaign-products")
def save_product(product: Product, request: Request):
    from PIL import Image, ImageOps
    studio.video_guard(request)
    key = uuid.uuid4().hex
    folder = root() / key
    folder.mkdir()
    try:
        data = product.model_dump(mode="json", exclude={"images","interior_pdf"})
        for i, encoded in enumerate(product.images):
            if len(encoded) > 3_000_000:
                raise ValueError("The product image is too large.")
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as im:
                if im.width * im.height > 12_000_000:
                    raise ValueError("Please resize the image before saving.")
                pic = ImageOps.exif_transpose(im).convert("RGB")
                pic.thumbnail((1400,1600))
                pic.save(folder / f"{i}.jpg", quality=88)
        if product.interior_pdf:
            import pypdfium2 as pdfium
            from pypdfium2 import raw
            try:
                with pdfium.PdfDocument(base64.b64decode(product.interior_pdf,validate=True)) as pdf:
                    if not 2 <= len(pdf) <= 600:
                        raise ValueError("Use an interior with 2 to 600 pages.")
                    selected = min(1, len(pdf)-2)
                    for index in range(min(len(pdf)-1, 40)):
                        with closing(pdf[index]) as page:
                            if any(page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE])):
                                selected = index
                                break
                    for slot, index in enumerate((selected,selected+1), start=1):
                        with closing(pdf[index]) as page:
                            scale = min(2, 1400/max(page.get_size()))
                            bitmap = page.render(scale=scale)
                            bitmap.to_pil().convert("RGB").save(folder/f"{slot}.jpg",quality=88)
                            bitmap.close()
            except Exception as exc:
                raise ValueError("The interior PDF could not be read.") from exc
        with database() as db:
            db.execute("INSERT INTO products VALUES (?,?)",(key,json.dumps(data)))
    except (ValueError, OSError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422,"Check the cover image and optional interior PDF. Use an unlocked PDF with 2 to 600 pages.") from exc
    return {"id":key}


def build(job, product, images, lines):
    try:
        render_campaign(root()/job, product, images, lines)
        status = "ready"
    except EditorialHold:
        log.exception("Campaign held by editorial review")
        status = "held"
    except Exception:
        log.exception("Campaign render failed")
        status = "failed"
    finally:
        try:
            with database() as db:
                db.execute("UPDATE jobs SET status=? WHERE id=?",(status,job))
        finally:
            render_lock.release()


@router.post("/api/v1/ad-campaigns")
def create(payload: Campaign, request: Request):
    studio.video_guard(request)
    key, job = payload.product, payload.request_id.hex
    with database() as db:
        prior = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if prior:
            if prior["product"] != key:
                raise HTTPException(409,"This request belongs to another product.")
            return {"id":job}
        holiday = key == "holiday"
        if holiday:
            product = HOLIDAY
            images = [ASSETS/(s+".jpg") for s in ["prompt","prompt","art","cover"]]
        else:
            saved = db.execute("SELECT data FROM products WHERE id=?", (key,)).fetchone()
            if not saved:
                raise HTTPException(404,"Choose a saved product.")
            product = json.loads(saved["data"])
            photos = sorted((root()/key).glob("*.jpg"))
            images = [photos[0], photos[min(1,len(photos)-1)], photos[-1], photos[0]]
        if not render_lock.acquire(False):
            raise HTTPException(409,"Another campaign is rendering. Try again when it finishes.")
        folder = root()/job
        try:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= 100:
                raise HTTPException(409,"Campaign storage is full.")
            used = {r[0] for r in db.execute("SELECT variant FROM jobs WHERE product=? AND status IN ('ready','rendering')",(key,))}
            variant = next((i for i in range(3) if i not in used), None)
            if variant is None:
                raise HTTPException(409,"All three campaign openings have been used for this product.")
            studio.reserve_hosted_render()
            folder.mkdir()
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)",(job,key,variant,"rendering"))
            db.commit()
            threading.Thread(target=build,args=(job,product,images,plan(product,variant,holiday)),daemon=True).start()
        except Exception:
            render_lock.release()
            raise
    return {"id":job}


@router.on_event("startup")
def recover_interrupted_jobs():
    if (studio.storage()/"campaigns"/"campaigns.sqlite").exists():
        with database() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE status='rendering'")
            for row in db.execute("SELECT id FROM jobs WHERE status='ready'").fetchall():
                if not (root()/row["id"]/"editorial.json").is_file():
                    db.execute("UPDATE jobs SET status='legacy' WHERE id=?", (row["id"],))


@router.get("/api/v1/ad-campaigns/{job}")
def status(job: uuid.UUID, request: Request):
    studio.video_guard(request)
    with database() as db:
        row = db.execute("SELECT status FROM jobs WHERE id=?", (job.hex,)).fetchone()
    if not row:
        raise HTTPException(404,"Campaign was not found.")
    if row[0]=="legacy":
        return {"status":"held"}
    if row[0]=="ready" and not (root()/job.hex/"editorial.json").is_file():
        return {"status":"held"}
    metadata = root()/job.hex/"shorts.json"
    if row[0] == "ready" and metadata.is_file():
        return {"status": "ready", "shorts": json.loads(metadata.read_text(encoding="utf-8"))}
    return {"status":row[0]}


@router.get("/api/v1/ad-campaigns/{job}/{asset}")
def download(job: uuid.UUID, asset: Literal["video","kit"], request: Request):
    if status(job,request)["status"] != "ready":
        raise HTTPException(409,"The campaign is still awaiting a successful render.")
    if not verified(root()/job.hex, asset):
        raise HTTPException(409,"This export needs a fresh editorial review. Create a new campaign.")
    name = "video.mp4" if asset=="video" else "campaign.zip"
    return FileResponse(root()/job.hex/name, media_type="video/mp4" if asset=="video" else "application/zip",
                        filename=None if asset=="video" else "TrafficLift-Campaign.zip",headers={"Cache-Control":"private, no-store"})

"""Bounded local photo packaging. Never calls a generation provider."""
import hashlib
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont
from backend import pipeline_store as store, pipeline_providers as providers
from backend.photo_video_api import storage
from backend.social_exports_api import _ffmpeg


def package(campaign, source, variant=0):
    identity = hashlib.sha256(f'photo-v1:{campaign["id"]}:{source["sha256"]}:{variant}'.encode()).hexdigest()
    with store.database() as db:
        row = db.execute("SELECT payload FROM entities WHERE kind='creative' AND id=?", (identity,)).fetchone()
        if row:
            ids = store.unseal(row['payload'])['assets']
        else:
            ids = None
            store.write(db, 'creative', identity, {'state': 'rendering', 'assets': [], 'at': time.time()})
    if ids is not None:
        return [store.get('asset', aid) for aid in ids]
    root = storage() / 'pipeline' / 'creative'
    root.mkdir(parents=True, exist_ok=True)
    try:
        if providers.asset_hash(source) != source['sha256']:
            raise ValueError('Original product photo changed')
        with Image.open(providers.asset_path(source)) as original:
            photo = original.convert('RGB')
        assets = []
        for kind, (w, h) in [('image', (1000, 1500)), ('video', (720, 1280))]:
            canvas = Image.new('RGB', (w, h), '#f7f0e5')
            draw = ImageDraw.Draw(canvas)
            def label(value, y, px):
                for name in ['DejaVuSans.ttf', 'C:/Windows/Fonts/arial.ttf']:
                    try:
                        font = ImageFont.truetype(name, px)
                        break
                    except OSError:
                        font = ImageFont.load_default(size=px)
                while draw.textbbox((0, 0), value, font=font)[2] > w * .86 and px > 18:
                    px -= 1
                    font = font.font_variant(size=px)
                box = draw.textbbox((0, 0), value, font=font)
                draw.text(((w - box[2]) / 2, y), value, font=font, fill='#1b3f30')
            label('#AD · PRODUCT OVERVIEW', int(h * .07), int(w * .027))
            label('A closer look' if variant == 0 else 'Will it fit your setup?', int(h * .15), int(w * .065))
            label(campaign['title'][:65], int(h * .23), int(w * .037))
            fitted = photo.copy()
            fitted.thumbnail((int(w * .86), int(h * .46)))
            canvas.paste(fitted, ((w - fitted.width) // 2, int(h * .32) + (int(h * .46) - fitted.height) // 2))
            label('Check size, care and delivery.' if variant == 0 else 'Compare the seller-listed details.', int(h * .81), int(w * .036))
            aid = hashlib.sha256((identity + kind).encode()).hexdigest()[:32]
            value = {'id': aid, 'campaign_id': campaign['id'], 'kind': kind, 'source_asset_id': source['id'],
                     'variant': variant, 'synthetic_media': source.get('synthetic_media', False), 'created_at': time.time(),
                     'content_type': 'image/jpeg' if kind == 'image' else 'video/mp4'}
            path = providers.asset_path(value)
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == 'image':
                canvas.save(path, format='JPEG', quality=95)
                value['technical'] = {'width': w, 'height': h}
            else:
                frame = root / (aid + '.png')
                canvas.save(frame)
                subprocess.run([_ffmpeg(), '-v', 'error', '-loop', '1', '-framerate', '30', '-i', str(frame),
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '12', '-c:v', 'libx264', '-preset', 'fast',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', '-f', 'mp4', '-y', str(path)],
                    capture_output=True, check=True, timeout=180)
                from backend.publishing_api import inspect_media
                value['technical'] = inspect_media(path)
            value['sha256'] = providers.asset_hash(value)
            store.put('asset', aid, value)
            assets.append(value)
        store.put('creative', identity, {'state': 'ready', 'assets': [a['id'] for a in assets]})
        return assets
    except Exception:
        store.put('creative', identity, {'state': 'needs_attention', 'assets': [], 'at': time.time()})
        raise

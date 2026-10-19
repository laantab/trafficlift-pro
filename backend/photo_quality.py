"""Source-image checks and exact-asset resolution variants; no generated product claims."""
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import re


def resolution_variants(url):
    p=urlsplit(url);host=(p.hostname or '').lower();variants=[]
    if host.endswith('.media-amazon.com') or host=='images-na.ssl-images-amazon.com':
        path=re.sub(r'\._[^/]+_\.', '._SL1500_.',p.path)
        if path!=p.path:variants.append(urlunsplit((p.scheme,p.netloc,path,p.query,'')))
    elif host in {'target.scene7.com','i5.walmartimages.com'}:
        params=dict(parse_qsl(p.query,keep_blank_values=True))
        if host=='target.scene7.com':params.update(wid='1400',hei='1400')
        else:params.update(odnWidth='1400',odnHeight='1400')
        variants.append(urlunsplit((p.scheme,p.netloc,p.path,urlencode(params),'')))
    return list(dict.fromkeys(variants+[url]))


def assess_photo(path):
    from PIL import Image,ImageOps
    import numpy as np
    with Image.open(path) as source:
        if source.width*source.height>40_000_000:raise ValueError('Product photo exceeds safe decode size.')
        im=ImageOps.exif_transpose(source).convert('RGB')
        width,height=im.size
        gray=np.asarray(ImageOps.contain(im.convert('L'),(512,512)),dtype=float)
        spread=float(gray.std())
        edges=float(np.abs(np.diff(gray,axis=0)).mean()+np.abs(np.diff(gray,axis=1)).mean())
        concerns=[]
        if min(width,height)<800:concerns.append('Source is below 800 pixels on its short side; close-ups may look soft.')
        if spread<2:concerns.append('Source appears nearly blank.')
        if edges<.8:concerns.append('Low detail detected; check focus and contrast.')
        return dict(width=width,height=height,pixels=width*height,detail_score=round(edges,2),
                    concerns=concerns,status='NEEDS_REVIEW' if concerns else 'SOURCE_CHECKS_PASS',
                    review_scope='Resolution and image-detail heuristics only; product accuracy, marks and composition require visual review.')


def choose_photos(paths):
    candidates=[]
    for path in paths:
        try:
            report=assess_photo(path)
            if min(report['width'],report['height'])>=400 and 'Source appears nearly blank.' not in report['concerns']:
                candidates.append((Path(path),report))
        except (OSError,ValueError):continue
    if not candidates:raise ValueError('No usable product photo could be downloaded from this listing.')
    candidates.sort(key=lambda item:(not item[1]['concerns'],min(item[1]['width'],item[1]['height']),item[1]['detail_score']),reverse=True)
    return candidates

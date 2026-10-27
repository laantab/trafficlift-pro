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



def _photo_fingerprint(path):
    """Small perceptual signature for URL variants of the SAME listing photo.

    A higher-resolution JPEG is not a second camera angle. Compare normalized
    pixels AND a difference hash; both must agree before suppressing a source.
    This deliberately favors keeping genuinely different views and colors.
    """
    from PIL import Image, ImageOps
    import numpy as np
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        aspect = image.width / image.height
        pixels = np.asarray(
            ImageOps.pad(image, (48, 48), color='white', method=Image.Resampling.LANCZOS),
            dtype=np.int16,
        )
        gray = np.asarray(
            ImageOps.pad(image.convert('L'), (9, 8), color=255, method=Image.Resampling.LANCZOS),
            dtype=np.int16,
        )
        return aspect, pixels, gray[:, 1:] > gray[:, :-1]


def _same_catalog_photo(first, second):
    import numpy as np
    if abs(first[0] / second[0] - 1) > .03:
        return False
    return (np.count_nonzero(first[2] != second[2]) <= 5
            and float(np.abs(first[1] - second[1]).mean()) <= 7.0)


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
    # Resolution variants of a single catalog photo used to fill every scene,
    # falsely reporting multiple approved photos. Keep the sharpest copy once.
    distinct=[]
    fingerprints=[]
    for path, report in candidates:
        try:
            fingerprint=_photo_fingerprint(path)
        except (OSError,ValueError):
            continue
        if any(_same_catalog_photo(fingerprint, seen) for seen in fingerprints):
            continue
        distinct.append((path,report))
        fingerprints.append(fingerprint)
    if not distinct:
        raise ValueError('No readable product photo could be selected.')
    return distinct

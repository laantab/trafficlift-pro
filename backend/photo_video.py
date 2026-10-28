"""Owned CPU photo-ad compositor. No provider calls; dependencies load on demand."""
from pathlib import Path
import json, os, shutil, subprocess, math, bisect
from backend.sales_script import build_sales_plan, creative_copy_gate, marketing_readiness_gate


def dependencies():
    # Hosted runtime can provision ffmpeg/ffprobe without privileged apt installs.
    # Local Windows installations keep using their existing ffmpeg binaries.
    if os.environ.get('TRAFFICLIFT_HOSTED_VIDEO') == '1':
        import static_ffmpeg
        static_ffmpeg.add_paths(weak=True)
    model = Path(os.environ.get('TRAFFICLIFT_VOICE_MODEL', 'models/kokoro-v1.0.int8.onnx'))
    voices = Path(os.environ.get('TRAFFICLIFT_VOICES', 'models/voices-v1.0.bin'))
    if not model.is_file() or not voices.is_file():
        raise ValueError('Free video voice setup is incomplete. Run Start_Free_Video_Studio.bat.')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('FFmpeg and FFprobe are required. Run Start_Free_Video_Studio.bat.')
    return model, voices


def crop_uniform_photo_margin(photo):
    """Tighten obvious plain-color listing backgrounds without cropping a product.

    Cropping is conservative: all four corners must share the same neutral
    background and the foreground bounding box must have adequate resolution.
    Photos with lifestyle backgrounds or ambiguous edges are left unchanged.
    """
    from PIL import Image, ImageChops
    if photo.mode != 'RGB':
        photo = photo.convert('RGB')
    w, h = photo.size
    if min(w, h) < 500:
        return photo
    corners = [photo.getpixel(p) for p in [(0,0), (w-1,0), (0,h-1), (w-1,h-1)]]
    reference = corners[0]
    if max(max(abs(a-b) for a,b in zip(reference, sample)) for sample in corners[1:]) > 12:
        return photo
    if max(reference) - min(reference) > 18:
        return photo
    background = Image.new('RGB', photo.size, reference)
    difference = ImageChops.difference(photo, background).convert('L')
    bbox = difference.point(lambda v: 255 if v > 25 else 0).getbbox()
    if not bbox:
        return photo
    x0, y0, x1, y1 = bbox
    if x0 < 5 or y0 < 5 or x1 > w-5 or y1 > h-5:
        return photo
    pad_x = max(20, round((x1-x0) * .07))
    pad_y = max(20, round((y1-y0) * .07))
    bounds = (max(0,x0-pad_x), max(0,y0-pad_y), min(w,x1+pad_x), min(h,y1+pad_y))
    width, height = bounds[2]-bounds[0], bounds[3]-bounds[1]
    if min(width, height) < 400 or width * height > .85 * w * h:
        return photo
    return photo.crop(bounds)



def validate_video_photo_resolution(size, max_upscale=1.35):
    """Refuse blurry video heroes disguised as 1080p by enlarging thumbnails.

    The real product photo is contained inside an 888x888 panel. Preserve its
    original aspect ratio and do not invent detail with a high-resolution MP4.
    """
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError('Product photo has invalid dimensions.')
    scale = min(888 / width, 888 / height)
    if scale > max_upscale:
        raise ValueError(
            'Product photo resolution is too low for a clear video. '
            'Use a higher-resolution verified photo of this exact product.'
        )
    return round(scale, 3)


def balanced_caption_chunks(phrase, max_words=7):
    """Preserve the spoken words while avoiding orphaned one-word subtitles."""
    words = str(phrase or '').split()
    if not words:
        return []
    count = math.ceil(len(words) / max_words)
    sizes = [len(words) // count + (1 if i < len(words) % count else 0)
             for i in range(count)]
    result = []
    cursor = 0
    for size in sizes:
        result.append(' '.join(words[cursor:cursor + size]))
        cursor += size
    return result


ASS_CAPTION_FONT_SIZE = 66
ASS_CAPTION_MAX_WIDTH = 740  # Room for ASS outline and platform side controls.


def ass_bgr(hex_color):
    """Convert the selected theme ink/background into libass BGR color."""
    value = hex_color.lstrip('#')
    if len(value) != 6 or any(c not in '0123456789abcdefABCDEF' for c in value):
        raise ValueError('Invalid caption theme color.')
    return '&H00' + value[4:6] + value[2:4] + value[0:2]


def measured_caption_chunks(phrase, measure):
    """Wrap spoken words using the actual ASS subtitle font, not a smaller font.

    Each subtitle has at most two lines within the vertical master's safe width.
    Wide text becomes more timed chunks instead of clipping or losing words.
    """
    def fit(words):
        full = ' '.join(words)
        if measure(full) <= ASS_CAPTION_MAX_WIDTH:
            return [[full]]
        if len(words) == 1:
            raise ValueError('A subtitle word is too wide for the video.')
        choices = []
        for cut in range(1, len(words)):
            left, right = ' '.join(words[:cut]), ' '.join(words[cut:])
            left_width, right_width = measure(left), measure(right)
            if max(left_width, right_width) <= ASS_CAPTION_MAX_WIDTH:
                orphan = int(min(cut, len(words)-cut) == 1 and len(words) >= 4)
                choices.append((orphan, abs(left_width-right_width), [left, right]))
        if choices:
            return [min(choices, key=lambda choice: choice[:2])[2]]
        middle = len(words) // 2
        return fit(words[:middle]) + fit(words[middle:])

    return [lines for chunk in balanced_caption_chunks(phrase, max_words=4)
            for lines in fit(chunk.split())]


def check_narration_audio(path):
    """Reject a silent, clipped or nearly inaudible spoken track before MP4 export."""
    import numpy as np
    import soundfile as sf
    audio, rate = sf.read(str(path), dtype='float32', always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if rate < 16000 or len(audio) < rate:
        raise ValueError('Narration audio is missing or too short.')
    if not np.isfinite(audio).all():
        raise ValueError('Narration audio contains invalid samples.')
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio))))
    if peak < 0.012 or rms < 0.001:
        raise ValueError('Narration is silent or too quiet for a product video.')
    if peak >= .999:
        raise ValueError('Narration appears clipped.')
    return {'status':'PASS','peak':round(peak,4),'rms':round(rms,5),'sample_rate':int(rate)}


def build_scene_storyboard(sales_plan, available_photos, detail_available=None):
    """Plan honest vertical-photo scenes without creating imaginary demonstrations.

    Returns serializable scene metadata used by the renderer and final QA.
    Additional photos must already be identity-matched and approved upstream.
    """
    if available_photos < 1:
        raise ValueError("A real matching product photo is required.")
    phrases = sales_plan.get("phrases") or []
    captions = sales_plan.get("captions") or []
    if not phrases or len(phrases) != len(captions):
        raise ValueError("Narration and scene captions must match.")
    stages = ["hook", "product", "benefit", "buying_details", "cta"]
    details = list(detail_available or [])
    storyboard = []
    for i, (phrase, caption) in enumerate(zip(phrases, captions)):
        photo = i % available_photos
        closeup = (i in (1, 2) if available_photos > 1 else i == 1) and photo < len(details) and bool(details[photo])
        storyboard.append({
            "scene": i + 1,
            "stage": (sales_plan.get("scenes") or [{}] * len(phrases))[i].get("stage", stages[min(i, len(stages)-1)])
                if i < len(sales_plan.get("scenes") or []) else stages[min(i, len(stages)-1)],
            "caption": caption,
            "narration": phrase,
            "photo_index": photo,
            "photo_treatment": "real_photo_detail_crop" if closeup else "real_photo_full",
            # A single licensed catalog photo cannot truthfully provide multiple
            # camera angles. Show it for the product reveal and final CTA, use
            # at most one genuine high-resolution detail crop where available, and make
            # every other interior scene a seller-supported fact card. This
            # prevents three or four near-identical catalog shots in one ad.
            "layout": ("seller_fact_card" if available_photos == 1
                       and 0 < i < len(phrases) - 1 and not closeup
                       else "photo_hero"),
            "motion": "gentle_pan_zoom",
            "transition": "fade" if i else "opening",
            "demonstration": False,
        })
    return storyboard


def render(image_path, directory, name, benefit, destination, seconds, style, progress, sales_plan=None, extra_images=None):
    sales_plan = sales_plan or build_sales_plan(name, benefit, destination, seconds)
    creative_copy_gate(sales_plan)
    marketing_readiness_gate(sales_plan)
    if sales_plan["review"]["status"] != "PASS":
        raise ValueError("The sales script has not passed editorial review.")
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
    import numpy as np
    import soundfile as sf
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    model, voices = dependencies()
    root = Path(directory)
    palette = {'warm': ('#221D1A','#FFF5E9','#F3B579'),
               'clean': ('#F4F5EF','#173C32','#BADBCC'),
               'bold': ('#101B30','#FFFFFF','#80DDD0')}[style]
    bg, ink, accent = palette
    font_path = os.environ.get('TRAFFICLIFT_FONT')
    if not font_path:
        font_path = 'C:/Windows/Fonts/arial.ttf' if os.name == 'nt' else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    def font(size): return ImageFont.truetype(font_path, size)
    def wrap(value, size, width=888):
        lines=['']
        for word in value.split():
            candidate=(lines[-1]+' '+word).strip()
            if font(size).getlength(word)>width: raise ValueError('Text contains a word too wide for the video.')
            if font(size).getlength(candidate)>width: lines.append(word)
            else: lines[-1]=candidate
        return lines
    title_lines=wrap(sales_plan['product'],52)
    if len(title_lines)>3: raise ValueError('Product name is too long. Shorten it before rendering.')
    options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
    engine=Kokoro.from_session(ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider']),str(voices))
    phrases=sales_plan['phrases']
    (root/'sales_script.json').write_text(json.dumps(sales_plan,indent=2),encoding='utf-8')
    progress('Making video — recording narration')
    clips=[];rate=24000
    for phrase in phrases:
        audio,rate=engine.create(phrase,voice='af_heart',speed=1.08,lang='en-us');clips.append(audio)
    lengths=[len(a)/rate for a in clips]
    # Use every recorded phrase. Natural length replaces long silent holds
    # and can extend the requested target instead of cutting the sales script.
    # Respect the requested 15/30/60 preset when the measured narration fits.
    # If it does not, extend safely rather than cut speech or silently truncate.
    needed=math.ceil(sum(lengths)+.22*(len(lengths)-1)+1.6)
    seconds=max(int(seconds),15,needed)
    if seconds>120:raise ValueError('The script exceeds two minutes. Shorten the product facts.')
    starts=narration_starts(lengths, seconds)
    voice=np.zeros(rate*seconds,dtype=np.float32)
    for a,t in zip(clips,starts): voice[round(t*rate):round(t*rate)+len(a)]=a
    sf.write(root/'voice.wav',voice,rate)
    narration_qa=check_narration_audio(root/'voice.wav')
    time=np.arange(rate*seconds)/rate
    music=sum(np.sin(2*np.pi*f*time)*.012 for f in [130.81,164.81,196])
    music*=np.minimum(1,time/.4)*np.minimum(1,(seconds-time)/.8)
    sf.write(root/'music.wav',music,rate)
    def stamp(t):
        cs=round(t*100);return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
    caption_family = font(ASS_CAPTION_FONT_SIZE).getname()[0].replace(',', ' ')
    caption_primary, caption_outline = ass_bgr(ink), ass_bgr(bg)
    ass=('[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n[V4+ Styles]\n'
         'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
         f'Style: Default,{caption_family},{ASS_CAPTION_FONT_SIZE},{caption_primary},{caption_primary},{caption_outline},{caption_outline},-1,0,0,0,100,100,0,0,1,4,1,2,120,120,110,1\n'
         '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    # Split long captions into readable timed chunks. Phrase-level approximate alignment.
    for phrase,t,duration in zip(phrases,starts,lengths):
        chunks=measured_caption_chunks(phrase, font(ASS_CAPTION_FONT_SIZE).getlength)
        total=sum(len(line.split()) for chunk in chunks for line in chunk);offset=0
        for lines in chunks:
            chunk_duration=duration*sum(len(line.split()) for line in lines)/total
            safe=r'\N'.join(lines).replace('{','').replace('}','')
            ass+=f'Dialogue: 0,{stamp(t+offset)},{stamp(t+offset+chunk_duration)},Default,,0,0,0,,'+safe+'\n'
            offset+=chunk_duration
    (root/'captions.ass').write_text(ass,encoding='utf-8')
    fitted_photos=[];detail_photos=[]
    for path in [image_path]+list(extra_images or [])[:3]:
        try:
            with Image.open(path) as source:
                if source.width*source.height>40_000_000:raise ValueError('Product photo is too large to decode safely.')
                photo=crop_uniform_photo_margin(ImageOps.exif_transpose(source).convert('RGB'))
                if min(photo.size)<400:raise ValueError('Product photo is too small. Choose a photo at least 400 pixels on each side.')
                validate_video_photo_resolution(photo.size)
                fitted_photos.append(ImageOps.contain(photo,(888,888),Image.Resampling.LANCZOS))
                if min(photo.size)>=1400:
                    w,h=photo.size
                    detail=photo.crop((round(w*.175),round(h*.175),round(w*.825),round(h*.825)))
                    detail_photos.append(ImageOps.contain(detail,(888,888),Image.Resampling.LANCZOS))
                else:detail_photos.append(None)
        except (ValueError,OSError):
            if path==image_path:raise
    if not fitted_photos:raise ValueError('A real product photo is required.')
    storyboard = build_scene_storyboard(sales_plan, len(fitted_photos), [d is not None for d in detail_photos])
    # A soft-focus version of the SAME verified photo fills the background.
    # This is a graphic treatment, never an invented lifestyle scene.
    backdrops = []
    for photo in fitted_photos:
        backdrop = ImageOps.fit(photo, (1080, 1920), method=Image.Resampling.BICUBIC)
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(85))
        backdrops.append(Image.blend(backdrop, Image.new('RGB', (1080, 1920), bg), .74))
    (root/'scene_storyboard.json').write_text(json.dumps(storyboard,indent=2),encoding='utf-8')
    def text(d,xy,value,size=44,color=ink):
        box=d.textbbox(xy,value,font=font(size))
        if not (90<=box[0] and box[2]<=990 and 120<=box[1] and box[3]<=1635):raise ValueError('Text failed the safe-margin check.')
        d.text(xy,value,font=font(size),fill=color)
    boundaries=[0]+[start-.05 for start in starts[1:]]+[seconds]
    stage_labels = {'hook': 'A CLOSER LOOK', 'product': 'MEET THE PRODUCT', 'benefit': 'SELLER-LISTED DETAILS', 'routine': 'IN YOUR ROUTINE', 'buying_details': 'BEFORE YOU BUY', 'cta': 'SEE THE LISTING'}
    def frame(t):
        scene=min(len(phrases)-1,max(0,bisect.bisect_right(boundaries,t)-1))
        local=(t-boundaries[scene])/(boundaries[scene+1]-boundaries[scene])
        im=backdrops[storyboard[scene]['photo_index']].copy()
        d=ImageDraw.Draw(im)
        # Structured graphic panels replace the old large empty background.
        d.rounded_rectangle((62,100,1018,455),radius=38,fill=bg)
        d.rounded_rectangle((48,470,1032,1420),radius=45,fill=accent)
        d.rounded_rectangle((65,487,1015,1403),radius=35,fill=bg)
        if storyboard[scene]['layout'] != 'seller_fact_card':
            d.rounded_rectangle((62,1425,1018,1635),radius=35,fill=bg)
        # Narration subtitles need their own high-contrast backing card.
        # A two-line 66px ASS caption with MarginV=110 sits at ~1650-1810;
        # the old footer stopped at 1635 and left words on the photo backdrop.
        d.rounded_rectangle((62,1625,1018,1855),radius=35,fill=bg)
        stage = storyboard[scene]['stage']
        text(d,(96,145),stage_labels.get(stage, 'PRODUCT DETAILS'),28)
        # Use the full product title only for the opening shot, preventing
        # five nearly identical title cards.
        if scene == 0 or storyboard[scene]['layout'] == 'seller_fact_card':
            # Fact cards show product identity once above the unique seller
            # claim. Never repeat that same claim as heading, card and footer.
            for i,line in enumerate(title_lines):
                text(d,(96,245+i*68),line,52)
        else:
            # Every photo scene title comes from the product-specific reviewed plan,
            # never an interchangeable template heading.
            scene_heading = storyboard[scene]['caption']
            heading_lines = wrap(scene_heading,48)
            if len(heading_lines)>2:raise ValueError('Scene heading does not fit.')
            for row,line in enumerate(heading_lines):
                text(d,(96,245+row*62),line,48)
        # A sizeable, alternating push/pull across real listing photos.
        # The whole product stays within the panel at every frame.
        photo_index=storyboard[scene]['photo_index']
        if storyboard[scene]['layout'] == 'seller_fact_card':
            # With only one licensed photo, alternate truthful typography
            # instead of showing the identical catalog image five times.
            d.rounded_rectangle((112,550,968,1325),radius=46,fill=accent)
            card_color = bg if bg in ('#221D1A','#101B30') else ink
            card_lines=wrap(storyboard[scene]['narration'],54,750)
            if len(card_lines)>8:raise ValueError('Seller fact card is too long.')
            first_row_y = max(620, 930 - len(card_lines)*38)
            for row,line in enumerate(card_lines):
                text(d,(150,first_row_y+row*76),line,54,card_color)
        else:
            fitted=(detail_photos[photo_index] if storyboard[scene]['photo_treatment']=='real_photo_detail_crop' else fitted_photos[photo_index])
            width,height,x,y=scene_geometry(fitted.size,scene,local)
            moving=fitted.resize((width,height),Image.Resampling.LANCZOS)
            im.paste(moving,(x,y))
        d=ImageDraw.Draw(im)
        labels=storyboard[scene]['caption']
        if storyboard[scene]['layout'] == 'seller_fact_card':
            # The seller claim is already large and centered in the card.
            # Do not echo it in a third footer or create an empty footer panel.
            pass
        elif scene==len(phrases)-1:
            # Reserve the subtitle zone below y=1578. Do not stack the
            # duplicate footer label, CTA and two-line narration subtitles.
            d.rounded_rectangle((96,1450,790,1540),radius=26,fill=accent)
            cta_ink = bg if bg in ('#221D1A','#101B30') else ink
            text(d,(130,1467),'View product details',44,cta_ink)
        else:
            label_lines=wrap(labels,42)
            if len(label_lines)>2:raise ValueError('Shorten the buyer need so its caption fits.')
            for row,line in enumerate(label_lines):text(d,(96,1435+row*52),line,42)
        # Brief fade-in at each cut, without changing the product itself.
        elapsed=t-boundaries[scene]
        if elapsed<.22:
            im=Image.blend(Image.new('RGB',im.size,bg),im,max(0,min(1,elapsed/.22)))
        return im
    progress('Making video — composing scenes')
    base=root/'base.mp4';process=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1080x1920','-r','30','-i','pipe:0','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p',str(base)],stdin=subprocess.PIPE)
    try:
        for n in range(seconds*30):process.stdin.write(frame(n/30).tobytes())
    finally:
        process.stdin.close();code=process.wait()
    if code:raise ValueError('Video composition failed.')
    out=root/'video.mp4'
    # Relative subtitle path avoids Windows drive-letter escaping in FFmpeg filters.
    filters="[0:v]ass=captions.ass[v];[1:a]asplit=2[voice][trigger];[2:a][trigger]sidechaincompress=threshold=0.015:ratio=8:attack=15:release=350[bed];[bed][voice]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]"
    subprocess.run(['ffmpeg','-y','-v','error','-i','base.mp4','-i','voice.wav','-i','music.wav','-filter_complex',filters,'-map','[v]','-map','[a]','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-t',str(seconds),'-movflags','+faststart','video.mp4'],cwd=root,check=True,timeout=600)
    progress('Checking video')
    quality=check_video(out,seconds)
    quality['motion']=check_motion(out,seconds)
    quality['rendered_creative']=inspect_rendered_creative(out, seconds, root, subtitle_panel_color=bg)
    quality['narration_audio']=narration_qa
    quality['marketing_approval']='NOT_APPROVED: real product imagery and full video still need human review'
    quality.update(storyboard=storyboard, phase2_storyboard='PASS', visual_review_required=True, narration_word_count=sum(len(p.split()) for p in phrases), narration_seconds=round(sum(lengths),2), scene_count=len(phrases), photo_count=len(fitted_photos), detail_scene_count=sum(1 for i in [1,2] if detail_photos[i%len(fitted_photos)] is not None), requested_seconds=sales_plan.get('requested_seconds'), preset_extended=(seconds > int(sales_plan.get('requested_seconds') or seconds)), script=phrases,destination=destination,caption_alignment='Approximate chunk timing within measured phrases',claims=sales_plan['review']['evidence_status'],sales_review=sales_plan['review'],sales_plan=sales_plan)
    (root/'quality.json').write_text(json.dumps(quality,indent=2))
    for name in ['base.mp4','voice.wav','music.wav']: (root/name).unlink(missing_ok=True)
    return out



def inspect_rendered_creative(path, seconds, root, subtitle_panel_color=None):
    """Review real decoded frames, not merely MP4 metadata.

    Retain a six-frame contact sheet for the human release gate. A flat product
    panel or visually unchanged scene sequence fails instead of being called a
    polished ad. Synthetic CI fixtures are NOT proof of real-product accuracy.
    """
    from PIL import Image, ImageOps, ImageDraw, ImageStat, ImageChops, ImageColor
    times = [max(.2, min(seconds - .3, seconds * ratio))
             for ratio in (.07, .24, .41, .59, .78, .94)]
    thumbs = []
    for index, moment in enumerate(times):
        frame = root / f'creative-frame-{index+1:02}.jpg'
        subprocess.run(['ffmpeg','-y','-v','error','-ss',f'{moment:.2f}',
                        '-i',str(path),'-frames:v','1','-q:v','3',str(frame)],
                       check=True,timeout=45)
        with Image.open(frame) as raw:
            im=raw.convert('RGB')
            if subtitle_panel_color:
                expected = ImageColor.getrgb(subtitle_panel_color)
                actual = im.getpixel((90, 1740))
                if max(abs(a-b) for a,b in zip(actual, expected)) > 32:
                    raise ValueError('Creative quality failed: narration captions lack their backing card.')
            panel=im.crop((90,520,990,1360))
            if max(ImageStat.Stat(panel).stddev) < 9:
                raise ValueError('Creative quality failed: product panel is visually empty.')
            thumbs.append(ImageOps.contain(im,(270,480)))
    changes = []
    for left,right in zip(thumbs,thumbs[1:]):
        diff=ImageChops.difference(left,right)
        changes.append(sum(ImageStat.Stat(diff).mean)/3)
    if sum(delta > 2.5 for delta in changes) < 2:
        raise ValueError('Creative quality failed: video scenes look identical.')
    sheet=Image.new('RGB',(270*3,480*2),'#ffffff')
    for i,thumb in enumerate(thumbs):
        sheet.paste(thumb,((i%3)*270,(i//3)*480))
    sheet.save(root/'creative_contact_sheet.jpg',quality=90)
    subtitle_review = inspect_rendered_subtitles(path, seconds, root, subtitle_panel_color)
    return {'status':'PASS', 'sample_seconds':[round(t,2) for t in times],
            'distinct_transitions':sum(delta>2.5 for delta in changes),
            'contact_sheet':'creative_contact_sheet.jpg',
            'rendered_subtitles':subtitle_review,
            'limitation':'Visible subtitle pixels are checked; human review and exact-product photo validation remain required'}


def subtitle_ink_pixels(frame, background):
    """Count high-contrast subtitle pixels inside the real caption card.

    Ignore the rounded border, photo panel and scene labels. This is a
    deterministic visibility check, not OCR or an advertising-quality claim.
    """
    from PIL import Image, ImageChops
    region = frame.convert('RGB').crop((160, 1640, 920, 1840))
    delta = ImageChops.difference(region, Image.new('RGB', region.size, background))
    r, g, b = delta.split()
    maximum = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return maximum.point(lambda value: 255 if value > 48 else 0).histogram()[255]


def inspect_rendered_subtitles(path, seconds, root, panel_color):
    """Decode timed subtitle frames and fail if ASS captions did not render.

    Checking the card background alone missed a regression where the panel
    existed but its narration text was absent. Sample *within* ASS dialogue
    events, not at arbitrary scene times that can legitimately be silent.
    """
    from PIL import Image, ImageColor
    if not panel_color:
        raise ValueError('Creative quality failed: subtitle panel color is unavailable.')
    ass_path = root / 'captions.ass'
    if not ass_path.is_file():
        raise ValueError('Creative quality failed: narration subtitle file is missing.')

    def timestamp(value):
        hours, minutes, secs = value.split(':')
        return int(hours) * 3600 + int(minutes) * 60 + float(secs)

    events = []
    for line in ass_path.read_text(encoding='utf-8').splitlines():
        if not line.startswith('Dialogue: '):
            continue
        fields = line.split(',', 4)
        if len(fields) < 5:
            continue
        try:
            start, end = timestamp(fields[1]), timestamp(fields[2])
        except (ValueError, IndexError):
            continue
        if end - start >= .25 and start < seconds and end <= seconds + .1:
            events.append((start + end) / 2)
    if not events:
        raise ValueError('Creative quality failed: no timed narration subtitles to inspect.')
    count = min(6, len(events))
    sample_times = [events[round(i * (len(events) - 1) / max(1, count - 1))]
                    for i in range(count)]
    ink_counts = []
    background = ImageColor.getrgb(panel_color)
    for index, moment in enumerate(sample_times):
        frame_path = root / f'subtitle-check-{index+1:02}.jpg'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', f'{moment:.2f}',
                        '-i', str(path), '-frames:v', '1', '-q:v', '3', str(frame_path)],
                       check=True, timeout=45)
        with Image.open(frame_path) as frame:
            ink_counts.append(subtitle_ink_pixels(frame, background))
    if min(ink_counts) < 500:
        raise ValueError('Creative quality failed: narration subtitles are missing or unreadable in the rendered MP4.')
    return {'status':'PASS', 'sampled_captions':count,
            'minimum_contrast_pixels':min(ink_counts),
            'sample_seconds':[round(t,2) for t in sample_times]}


def check_video(path,seconds):
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],timeout=30))
    video=next(s for s in probe['streams'] if s['codec_type']=='video')
    audio=next((s for s in probe['streams'] if s['codec_type']=='audio'),None)
    if (video['width'],video['height'])!=(1080,1920) or video['codec_name']!='h264' or video.get('pix_fmt')!='yuv420p':raise ValueError('Video shape or format check failed.')
    if abs(float(probe['format']['duration'])-seconds)>.05:raise ValueError('Video length check failed.')
    if not audio or audio['codec_name']!='aac':raise ValueError('Audio check failed.')
    subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(path),'-f','null','-'],check=True,timeout=300)
    return {'status':'PASS','duration':seconds,'dimensions':[1080,1920],'decode':'PASS','text_bounds':'PASS every composed frame','social_upload':'Not tested'}


def check_motion(path,seconds):
    """Compare only the photo panel, so captions cannot fake motion PASS."""
    import numpy as np
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-vf','fps=1,crop=888:888:96:510,scale=96:96','-t',str(seconds),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'],timeout=180)
    frame_bytes=96*96*3
    if len(raw)<frame_bytes*3 or len(raw)%frame_bytes:raise ValueError('Video motion check could not read enough frames.')
    frames=np.frombuffer(raw,dtype=np.uint8).reshape(-1,96,96,3).astype(np.float32)
    differences=np.abs(frames[1:]-frames[:-1]).mean(axis=(1,2,3))
    moving=int(np.count_nonzero(differences>.25))
    if moving<max(2,int(len(differences)*.6)):raise ValueError('The product picture is staying still. Motion check failed.')
    return {'status':'PASS','changed_photo_intervals':moving,'sampled_intervals':len(differences)}



def narration_starts(lengths, seconds):
    """Keep closing CTA at the end; distribute pauses across reveal and benefit."""
    spare = seconds - sum(lengths) - .2
    if spare < .6:
        raise ValueError('Script is too long for this length. Choose a longer video.')
    final_hold = min(1.2, spare / 3)
    pause = (spare - final_hold) / max(1,len(lengths)-1)
    starts=[];clock=.2
    for length in lengths:
        starts.append(clock);clock+=length+pause
    return starts


def scene_geometry(size,scene,phase):
    phase=max(0,min(1,phase))
    zoom=(.73+.27*phase) if scene%2==0 else (1-.27*phase)
    width=max(1,round(size[0]*zoom));height=max(1,round(size[1]*zoom))
    dx=round((phase-.5)*min(70,(888-width)/2))
    dy=round((.5-phase)*min(36,(888-height)/2))
    return width,height,(1080-width)//2+dx,510+(888-height)//2+dy

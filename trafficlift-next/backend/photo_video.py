"""Owned CPU photo-ad compositor. No provider calls; dependencies load on demand."""
from pathlib import Path
import json, os, shutil, subprocess, math, bisect
from backend.sales_script import build_sales_plan


def dependencies():
    model = Path(os.environ.get('TRAFFICLIFT_VOICE_MODEL', 'models/kokoro-v1.0.int8.onnx'))
    voices = Path(os.environ.get('TRAFFICLIFT_VOICES', 'models/voices-v1.0.bin'))
    if not model.is_file() or not voices.is_file():
        raise ValueError('Free video voice setup is incomplete. Run Start_Free_Video_Studio.bat.')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('FFmpeg and FFprobe are required. Run Start_Free_Video_Studio.bat.')
    return model, voices


def render(image_path, directory, name, benefit, destination, seconds, style, progress, sales_plan=None, extra_images=None):
    sales_plan = sales_plan or build_sales_plan(name, benefit, destination, seconds)
    if sales_plan["review"]["status"] != "PASS":
        raise ValueError("The sales script has not passed editorial review.")
    from PIL import Image, ImageDraw, ImageFont, ImageOps
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
    measured_boxes=[]
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
    required=math.ceil(sum(lengths)+.22*(len(lengths)-1)+1.6)
    if required>seconds:raise ValueError('Narration does not fit the selected duration. Shorten the facts or select a longer video.')
    starts=narration_starts(lengths, seconds)
    voice=np.zeros(rate*seconds,dtype=np.float32)
    for a,t in zip(clips,starts): voice[round(t*rate):round(t*rate)+len(a)]=a
    sf.write(root/'voice.wav',voice,rate)
    time=np.arange(rate*seconds)/rate
    music=sum(np.sin(2*np.pi*f*time)*.012 for f in [130.81,164.81,196])
    music*=np.minimum(1,time/.4)*np.minimum(1,(seconds-time)/.8)
    sf.write(root/'music.wav',music,rate)
    def stamp(t):
        cs=round(t*100);return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
    ass='[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,38,&H00FFFFFF,&H00FFFFFF,&H001A1D22,&H001A1D22,0,0,0,0,100,100,0,0,3,12,0,2,110,110,205,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    # Split long captions into readable timed chunks. Phrase-level approximate alignment.
    for phrase,t,duration in zip(phrases,starts,lengths):
        words=phrase.split();chunks=[]
        for i in range(0,len(words),7):chunks.append(' '.join(words[i:i+7]))
        total=sum(len(c.split()) for c in chunks);offset=0
        for chunk in chunks:
            chunk_duration=duration*len(chunk.split())/total
            lines=wrap(chunk,38,840)
            if len(lines)>2:raise ValueError('Caption cannot fit safely.')
            safe=r'\N'.join(lines).replace('{','').replace('}','')
            ass+=f'Dialogue: 0,{stamp(t+offset)},{stamp(t+offset+chunk_duration)},Default,,0,0,0,,'+safe+'\n'
            offset+=chunk_duration
    (root/'captions.ass').write_text(ass,encoding='utf-8')
    fitted_photos=[];detail_photos=[]
    for path in [image_path]+list(extra_images or [])[:3]:
        try:
            with Image.open(path) as source:
                if source.width*source.height>40_000_000:raise ValueError('Product photo is too large to decode safely.')
                photo=ImageOps.exif_transpose(source).convert('RGB')
                if min(photo.size)<400:raise ValueError('Product photo is too small. Choose a photo at least 400 pixels on each side.')
                fitted_photos.append(ImageOps.contain(photo,(800,800),Image.Resampling.LANCZOS))
                if min(photo.size)>=1400:
                    w,h=photo.size
                    detail=photo.crop((round(w*.175),round(h*.175),round(w*.825),round(h*.825)))
                    detail_photos.append(ImageOps.contain(detail,(800,800),Image.Resampling.LANCZOS))
                else:detail_photos.append(None)
        except (ValueError,OSError):
            if path==image_path:raise
    if not fitted_photos:raise ValueError('A real product photo is required.')
    def text(d,xy,value,size=44,color=ink):
        box=d.textbbox(xy,value,font=font(size))
        if not (90<=box[0] and box[2]<=990 and 120<=box[1] and box[3]<=1635):raise ValueError('Text failed the safe-margin check.')
        measured_boxes.append(tuple(box))
        d.text(xy,value,font=font(size),fill=color)
    boundaries=[0]+[start-.05 for start in starts[1:]]+[seconds]
    def frame(t):
        scene=min(len(phrases)-1,max(0,bisect.bisect_right(boundaries,t)-1))
        local=(t-boundaries[scene])/(boundaries[scene+1]-boundaries[scene])
        im=Image.new('RGB',(1080,1920),bg);d=ImageDraw.Draw(im)
        text(d,(96,145),'A CLOSER LOOK',25)
        for i,line in enumerate(title_lines):text(d,(96,245+i*68),line,52)
        # A sizeable, alternating push/pull across real listing photos.
        # The whole product stays within the panel at every frame.
        photo_index=scene % len(fitted_photos)
        fitted=(detail_photos[photo_index] if scene in {1,2} and detail_photos[photo_index] is not None else fitted_photos[photo_index])
        width,height,x,y=scene_geometry(fitted.size,scene,local)
        moving=fitted.resize((width,height),Image.Resampling.LANCZOS)
        im.paste(moving,(x,y))
        d=ImageDraw.Draw(im)
        labels=sales_plan['captions'][scene]
        label_lines=wrap(labels,36)
        if len(label_lines)>2:raise ValueError('Shorten the buyer need so its caption fits.')
        for row,line in enumerate(label_lines):text(d,(96,1410+row*45),line,36)
        if scene==len(phrases)-1:
            d.rounded_rectangle((96,1510,760,1610),radius=30,fill=accent)
            text(d,(130,1530),'View product details',40,bg)
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
        process.stdin.close()
        try:code=process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=10)
            raise ValueError('Video encoder timed out.')
    if code:raise ValueError('Video composition failed.')
    out=root/'video.mp4'
    # Relative subtitle path avoids Windows drive-letter escaping in FFmpeg filters.
    filters="[0:v]ass=captions.ass[v];[1:a]asplit=2[voice][trigger];[2:a][trigger]sidechaincompress=threshold=0.015:ratio=8:attack=15:release=350[bed];[bed][voice]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]"
    subprocess.run(['ffmpeg','-y','-v','error','-i','base.mp4','-i','voice.wav','-i','music.wav','-filter_complex',filters,'-map','[v]','-map','[a]','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-t',str(seconds),'-movflags','+faststart','video.mp4'],cwd=root,check=True,timeout=600)
    progress('Checking video')
    quality=check_video(out,seconds)
    quality['motion']=check_motion(out,seconds)
    quality['text_bounds']['measured_draws']=len(measured_boxes)
    quality['text_bounds']['safe_rectangle']=[90,120,990,1635]
    quality['text_bounds']['observed_rectangle']=[min(b[0] for b in measured_boxes),min(b[1] for b in measured_boxes),max(b[2] for b in measured_boxes),max(b[3] for b in measured_boxes)]
    quality['marketing_approval']='NOT_APPROVED: source and complete video need visual review'
    quality.update(narration_word_count=sum(len(p.split()) for p in phrases), narration_seconds=round(sum(lengths),2), scene_count=len(phrases), photo_count=len(fitted_photos), detail_scene_count=sum(1 for i in [1,2] if detail_photos[i%len(fitted_photos)] is not None), requested_seconds=sales_plan.get('requested_seconds'), script=phrases,destination=destination,caption_alignment='Approximate chunk timing within measured phrases',claims=sales_plan['review']['evidence_status'],sales_review=sales_plan['review'],sales_plan=sales_plan)
    (root/'quality.json').write_text(json.dumps(quality,indent=2))
    for name in ['base.mp4','voice.wav','music.wav']: (root/name).unlink(missing_ok=True)
    return out


def check_video(path,seconds):
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],timeout=30))
    video=next(s for s in probe['streams'] if s['codec_type']=='video')
    audio=next((s for s in probe['streams'] if s['codec_type']=='audio'),None)
    if (video['width'],video['height'])!=(1080,1920) or video['codec_name']!='h264' or video.get('pix_fmt')!='yuv420p':raise ValueError('Video shape or format check failed.')
    if abs(float(probe['format']['duration'])-seconds)>.05:raise ValueError('Video length check failed.')
    if not audio or audio['codec_name']!='aac':raise ValueError('Audio check failed.')
    subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(path),'-f','null','-'],check=True,timeout=300)
    return {'status':'PASS','duration':seconds,'dimensions':[1080,1920],'decode':'PASS','text_bounds':{'composed_text':'Measured with font bounding boxes on each frame','subtitle_layout':'Measured wrapping before encoding; final subtitle pixel bounds not independently verified'},'social_upload':'Not tested'}


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

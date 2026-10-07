"""Local installation gate: real voice + FFmpeg render, no remote requests."""
import os,sys,tempfile,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)

def verify():
    os.environ.setdefault('TRAFFICLIFT_VOICE_MODEL',str(ROOT/'models/kokoro-v1.0.int8.onnx'))
    os.environ.setdefault('TRAFFICLIFT_VOICES',str(ROOT/'models/voices-v1.0.bin'))
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        binary=next((ROOT/'.video-tools').rglob('ffmpeg.exe'),None)
        if binary:os.environ['PATH']=str(binary.parent)+os.pathsep+os.environ.get('PATH','')
    from backend.photo_video import render
    from backend.sales_script import build_sales_plan
    from PIL import Image,ImageDraw
    with tempfile.TemporaryDirectory(prefix='trafficlift-install-check-') as folder:
        directory=Path(folder);image=directory/'check.png'
        picture=Image.new('RGB',(1500,1500),'white');draw=ImageDraw.Draw(picture)
        draw.rounded_rectangle((480,150,1020,1360),radius=80,fill='#297c8e');draw.rectangle((500,120,1000,210),fill='#123e49');picture.save(image)
        plan=build_sales_plan('Installation Check','','https://example.com/installation-check',15)
        render(image,directory,'Installation Check','','https://example.com/installation-check',15,'warm',lambda message:print(message,flush=True),sales_plan=plan)
        quality=json.loads((directory/'quality.json').read_text())
        if quality['status']!='PASS':raise RuntimeError('Installation render did not pass.')
        print('INSTALLATION_CHECK_PASS: voice, captions, 1080x1920 H.264/AAC, duration and motion',flush=True)

if __name__=='__main__':
    try:verify()
    except Exception as exc:
        print('INSTALLATION_CHECK_FAILED: '+str(exc),file=sys.stderr,flush=True);sys.exit(1)

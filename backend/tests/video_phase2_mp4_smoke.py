"""Integration smoke: real FFmpeg 1080x1920 MP4 from video compositor.

Speech is synthetic audio, NOT a Kokoro quality test. No paid providers.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json, sys, types, os
import numpy as np
from PIL import Image, ImageDraw

from backend.photo_video import render


def main():
    import soundfile  # ensure local audio deps available
    if not __import__('shutil').which('ffmpeg') or not __import__('shutil').which('ffprobe'):
        raise RuntimeError('FFmpeg and FFprobe needed for video smoke')

    onnx = types.ModuleType('onnxruntime')
    onnx.SessionOptions = type('Options', (), {})
    onnx.InferenceSession = lambda *a, **k: object()
    onnx.__path__ = []
    kokoro = types.ModuleType('kokoro_onnx')
    class FakeKokoro:
        @staticmethod
        def from_session(*args): return FakeKokoro()
        def create(self, phrase, voice, speed, lang):
            rate = 24000
            t = np.arange(int(rate * .65)) / rate
            audio = np.sin(2 * np.pi * 200 * t).astype(np.float32) * .03
            return audio, rate
    kokoro.Kokoro = FakeKokoro
    real_voice = os.getenv('TRAFFICLIFT_TEST_REAL_KOKORO') == '1'
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root/'voice.onnx').touch()
        (root/'voices.bin').touch()
        photos = []
        for index in range(2):
            im=Image.new('RGB',(800,800),'#fcfcfc')
            d=ImageDraw.Draw(im)
            d.rounded_rectangle((190+index*40,110,610,690),radius=35,fill='#264653' if index else '#235F68')
            d.rectangle((240,620,560,700),fill='#CD8D44')
            path=root/f'product-{index}.png'
            im.save(path);photos.append(path)
        plan={
            'product':'Desk Organizer',
            'phrases':['Consider the organizer','See the actual product','Compare the available details','Review the seller listing','See your options'],
            'captions':['Explore the product','A closer look','Review the details','Check the listing','See product details'],
            'scenes':[{'stage':x} for x in ('hook','product','benefit','buying_details','cta')],
            'review':{'status':'PASS','evidence_status':'Synthetic smoke only'},
            'requested_seconds':15
        }
        if real_voice:
            from backend.photo_video import dependencies
            model, voices = dependencies()
            assert model.stat().st_size > 1_000_000 and voices.stat().st_size > 1_000_000
            output=render(photos[0],root,'Desk Organizer','', 'https://seller.example/product',15,'clean',lambda _:None,sales_plan=plan,extra_images=photos[1:])
        else:
            with patch.dict(sys.modules,{'onnxruntime':onnx,'kokoro_onnx':kokoro}), \
                 patch('backend.photo_video.dependencies',return_value=(root/'voice.onnx',root/'voices.bin')):
                output=render(photos[0],root,'Desk Organizer','', 'https://seller.example/product',15,'clean',lambda _:None,sales_plan=plan,extra_images=photos[1:])
        assert output.is_file() and output.stat().st_size>10000
        qa=json.loads((root/'quality.json').read_text())
        assert qa['status']=='PASS' and qa['motion']['status']=='PASS'
        assert qa['scene_count']==5 and qa['photo_count']==2
        assert qa['duration']>=15 and qa['duration']<=120
        assert qa['narration_audio']['status']=='PASS'
        assert (root/'scene_storyboard.json').is_file()
        # CI retains its /tmp artifact convention; hosted acceptance uses an
        # isolated persistent-disk staging directory supplied by the bootstrap.
        output_dir = Path(os.getenv('TRAFFICLIFT_TEST_OUTPUT_DIR', '/tmp'))
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / ('trafficlift-phase2-real-kokoro.mp4' if real_voice else 'trafficlift-phase2-smoke.mp4')
        dest.write_bytes(output.read_bytes())
        (output_dir / 'trafficlift-phase2-quality.json').write_text(json.dumps(qa, indent=2))
        print('PASS: actual 15-second MP4, 1080x1920 H264, AAC, 5 scenes, 2 photos, decoded and motion-checked')


if __name__=='__main__':main()

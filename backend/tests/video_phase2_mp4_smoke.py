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
        # Exercise the actual evidence-led copy builder, not a hand-authored
        # storyboard that can hide generic-template regressions.
        from backend.sales_script import build_sales_plan, marketing_readiness_gate
        plan = build_sales_plan(
            'Desk Organizer',
            'Tall rectangular body visible in this synthetic fixture',
            'https://seller.example/product', 120,
            buyer_need='Considering the Desk Organizer',
            feature_details=['Contrasting orange lower panel visible in this synthetic fixture'],
            evidence_origin='synthetic_fixture')
        assert marketing_readiness_gate(plan)['distinct_product_facts'] == 2
        assert not any('seller-listed details' in caption.lower() or
                       'size and options' in caption.lower() or
                       'review listed features' in caption.lower()
                       for caption in plan['captions'])
        plan['review']['evidence_status'] = 'Synthetic smoke only; not a real listing'
        plan['requested_seconds'] = 15
        if real_voice:
            from backend.photo_video import dependencies
            model, voices = dependencies()
            assert model.stat().st_size > 1_000_000 and voices.stat().st_size > 1_000_000
            output=render(photos[0],root,'Desk Organizer','', 'https://seller.example/product',15,'clean',lambda _:None,sales_plan=plan,extra_images=[])
        else:
            with patch.dict(sys.modules,{'onnxruntime':onnx,'kokoro_onnx':kokoro}), \
                 patch('backend.photo_video.dependencies',return_value=(root/'voice.onnx',root/'voices.bin')):
                output=render(photos[0],root,'Desk Organizer','', 'https://seller.example/product',15,'clean',lambda _:None,sales_plan=plan,extra_images=[])
        assert output.is_file() and output.stat().st_size>10000
        qa=json.loads((root/'quality.json').read_text())
        assert qa['status']=='PASS' and qa['motion']['status']=='PASS'
        assert qa['scene_count']==4 and qa['photo_count']==1
        assert sum(scene['layout']=='seller_fact_card' for scene in qa['storyboard'])==2
        assert qa['storyboard'][-1]['stage']=='cta'
        assert qa['storyboard'][-1]['layout']=='photo_hero'
        assert qa['duration']>=15 and qa['duration']<=120
        assert qa['narration_audio']['status']=='PASS'
        assert qa['rendered_creative']['status']=='PASS'
        assert qa['rendered_creative']['rendered_subtitles']['status']=='PASS'
        assert qa['rendered_creative']['rendered_subtitles']['sampled_captions'] >= 2
        assert qa['rendered_creative']['distinct_transitions'] >= 2
        assert (root/'creative_contact_sheet.jpg').is_file()
        assert (root/'scene_storyboard.json').is_file()
        # CI retains its /tmp artifact convention; hosted acceptance uses an
        # isolated persistent-disk staging directory supplied by the bootstrap.
        output_dir = Path(os.getenv('TRAFFICLIFT_TEST_OUTPUT_DIR', '/tmp'))
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / ('trafficlift-phase2-real-kokoro.mp4' if real_voice else 'trafficlift-phase2-smoke.mp4')
        dest.write_bytes(output.read_bytes())
        (output_dir / 'trafficlift-phase2-quality.json').write_text(json.dumps(qa, indent=2))
        (output_dir / ('trafficlift-phase2-real-kokoro-frames.jpg' if real_voice
                       else 'trafficlift-phase2-smoke-frames.jpg')).write_bytes(
                           (root/'creative_contact_sheet.jpg').read_bytes())
        print('PASS: actual 15-second MP4, 1080x1920 H264, AAC, 4 scenes, 1 photo + one fact card, decoded and motion-checked')


if __name__=='__main__':main()

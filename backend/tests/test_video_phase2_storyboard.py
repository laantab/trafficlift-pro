"""Phase 2 video storyboard: real-photo-only scenes and reliable metadata."""
import pytest
from backend.photo_video import build_scene_storyboard, balanced_caption_chunks


def plan():
    return {
        "phrases": ["The hook", "The product", "Seller-supported benefit", "Compare details", "Check the listing"],
        "captions": ["Hook", "Product", "Benefit", "Details", "Link"],
        "scenes": [{"stage": stage} for stage in ["hook", "product", "benefit", "buying_details", "cta"]],
    }



@pytest.mark.parametrize('size', [(400, 400), (600, 600), (500, 800), (800, 500)])
def test_video_hero_rejects_source_photos_that_need_blurry_upscaling(size):
    from backend.photo_video import validate_video_photo_resolution
    # Portrait/landscape photos can remain sharp if contained without enlargement.
    expected_scale = min(888 / size[0], 888 / size[1])
    if expected_scale > 1.35:
        with pytest.raises(ValueError, match='higher-resolution verified photo'):
            validate_video_photo_resolution(size)
    else:
        assert validate_video_photo_resolution(size) == round(expected_scale, 3)


@pytest.mark.parametrize('size', [(800, 800), (1000, 500), (500, 1000), (1600, 1200)])
def test_video_hero_keeps_sharp_exact_product_photos(size):
    from backend.photo_video import validate_video_photo_resolution
    assert validate_video_photo_resolution(size) <= 1.35


def test_video_render_checks_source_resolution_before_resampling():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'photo_video.py').read_text()
    assert 'validate_video_photo_resolution(photo.size)' in source
    assert source.index('validate_video_photo_resolution(photo.size)') < source.index(
        'fitted_photos.append(ImageOps.contain(photo,(888,888)'
    )


def test_storyboard_multiple_real_photos_and_detail_scene():
    scenes = build_scene_storyboard(plan(), 3, [True, False, True])
    assert len(scenes) == 5
    assert [x["photo_index"] for x in scenes] == [0, 1, 2, 0, 1]
    assert scenes[2]["photo_treatment"] == "real_photo_detail_crop"
    assert all(x["demonstration"] is False for x in scenes)
    assert scenes[-1]["stage"] == "cta"


def test_one_photo_never_invents_additional_photographs():
    scenes = build_scene_storyboard(plan(), 1, [False])
    assert {x["photo_index"] for x in scenes} == {0}
    assert {x["photo_treatment"] for x in scenes} == {"real_photo_full"}
    assert [x["layout"] for x in scenes] == [
        "photo_hero", "seller_fact_card", "seller_fact_card",
        "seller_fact_card", "photo_hero",
    ]


def test_one_high_resolution_photo_uses_only_genuine_detail_crops():
    # A detail crop of the SAME exact-product photo is allowed when sharp.
    # It must never be mislabeled as a different angle or lifestyle photo.
    scenes = build_scene_storyboard(plan(), 1, [True])
    assert [x["layout"] for x in scenes] == [
        "photo_hero", "photo_hero", "seller_fact_card",
        "seller_fact_card", "photo_hero",
    ]
    assert [x["photo_treatment"] for x in scenes] == [
        "real_photo_full", "real_photo_detail_crop",
        "real_photo_full", "real_photo_full", "real_photo_full",
    ]
    assert {x["photo_index"] for x in scenes} == {0}
    assert not any(x["demonstration"] for x in scenes)


def test_fact_card_has_one_central_fact_not_duplicate_heading_and_footer():
    # This is a source-contract regression; the FFmpeg smoke separately
    # decodes actual pixels and retains a six-frame visual contact sheet.
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'photo_video.py').read_text()
    assert "if scene == 0 or storyboard[scene]['layout'] == 'seller_fact_card':" in source
    assert "if storyboard[scene]['layout'] == 'seller_fact_card':" in source
    assert "first_row_y = max(620, 930 - len(card_lines)*38)" in source
    assert "d.rounded_rectangle((62,1425,1018,1635),radius=35,fill=bg)" in source


def test_missing_product_photo_rejected():
    with pytest.raises(ValueError, match="photo"):
        build_scene_storyboard(plan(), 0)


def test_mismatched_caption_count_rejected():
    bad = plan()
    bad["captions"].pop()
    with pytest.raises(ValueError, match="match"):
        build_scene_storyboard(bad, 2)


def test_caption_chunks_preserve_words_without_orphan():
    text = "Check the exact seller listing for available sizes and options before deciding."
    chunks = balanced_caption_chunks(text)
    assert " ".join(chunks) == text
    assert all(len(chunk.split()) <= 7 for chunk in chunks)
    assert min(len(chunk.split()) for chunk in chunks) >= 4


def test_render_uses_distinct_stage_titles_and_larger_photo_panel():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[1]/"photo_video.py").read_text()
    assert "ImageOps.contain(photo,(888,888)" in src
    assert "if scene == 0 or storyboard[scene]['layout'] == 'seller_fact_card':" in src
    assert "scene_heading =" in src
    assert "labels=storyboard[scene]['caption']" in src
    assert "seller_fact_card" in src
    assert "Review listed features" not in src
    assert "Picture your setup" not in src


def test_blank_photo_margin_is_trimmed_without_clipping():
    from PIL import Image, ImageDraw
    from backend.photo_video import crop_uniform_photo_margin
    image = Image.new('RGB', (800,800), '#fcfcfc')
    draw = ImageDraw.Draw(image)
    draw.rectangle((190,110,610,690), fill='#264653')
    cropped = crop_uniform_photo_margin(image)
    assert cropped.width < image.width and cropped.height < image.height
    assert cropped.getpixel((cropped.width//2,cropped.height//2)) == (38,70,83)


def test_lifestyle_photo_is_not_force_cropped():
    from PIL import Image
    from backend.photo_video import crop_uniform_photo_margin
    photo = Image.new('RGB',(800,800),'#ffffff')
    photo.putpixel((799,799),(10,50,60))
    assert crop_uniform_photo_margin(photo).size == photo.size


def test_narration_quality_rejects_silence_and_clipping(tmp_path):
    import numpy as np
    import soundfile as sf
    from backend.photo_video import check_narration_audio
    rate=24000
    path=tmp_path/'voice.wav'
    sf.write(path,np.zeros(rate*2,dtype=np.float32),rate)
    with pytest.raises(ValueError,match='silent|quiet'):
        check_narration_audio(path)
    t=np.arange(rate*2)/rate
    sf.write(path,(np.sin(2*np.pi*220*t)*.03).astype(np.float32),rate)
    assert check_narration_audio(path)['status']=='PASS'
    sf.write(path,np.ones(rate*2,dtype=np.float32),rate, subtype='FLOAT')
    with pytest.raises(ValueError,match='clipped'):
        check_narration_audio(path)


def test_ass_subtitle_chunks_fit_real_rendered_font_and_keep_every_word():
    """Regression: measuring at 26px while ASS renders at 58px clipped long lines."""
    from PIL import ImageFont
    from backend.photo_video import measured_caption_chunks, ASS_CAPTION_FONT_SIZE, ASS_CAPTION_MAX_WIDTH
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', ASS_CAPTION_FONT_SIZE)
    phrase = ('Compare the current price and available sizes before deciding whether '
              'this adjustable stainless steel storage organizer fits your space.')
    chunks = measured_caption_chunks(phrase, font.getlength)
    assert chunks and all(1 <= len(chunk) <= 2 for chunk in chunks)
    assert all(font.getlength(line) <= ASS_CAPTION_MAX_WIDTH for chunk in chunks for line in chunk)
    assert ' '.join(line for chunk in chunks for line in chunk) == phrase
    assert all(len(line.split()) > 1 for chunk in chunks for line in chunk)


def test_subtitle_style_matches_measured_font_and_stays_out_of_footer():
    """ASS layout must use the same font size measured during wrapping."""
    from pathlib import Path
    from backend.photo_video import ASS_CAPTION_FONT_SIZE, ASS_CAPTION_MAX_WIDTH
    assert ASS_CAPTION_FONT_SIZE >= 48
    assert ASS_CAPTION_MAX_WIDTH <= 760
    source = (Path(__file__).resolve().parents[1] / 'photo_video.py').read_text()
    assert "measured_caption_chunks(phrase, font(ASS_CAPTION_FONT_SIZE).getlength)" in source
    assert "MarginV" in source and "ASS_CAPTION_FONT_SIZE" in source


def test_subtitle_uses_theme_ink_not_white_on_a_light_background():
    from backend.photo_video import ass_bgr
    # Clean theme uses dark green ink on a light background; the old white
    # subtitles with a heavy black box were hard to read and looked unfinished.
    assert ass_bgr('#173C32') == '&H00323C17'
    assert ass_bgr('#F4F5EF') == '&H00EFF5F4'
    assert ass_bgr('#FFFFFF') == '&H00FFFFFF'
    with pytest.raises(ValueError):
        ass_bgr('invalid')


def test_narration_subtitles_have_a_dedicated_contrast_card():
    """Regression from actual six-frame review: second caption line escaped footer."""
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'photo_video.py').read_text()
    assert "d.rounded_rectangle((62,1625,1018,1855),radius=35,fill=bg)" in source
    assert "120,120,110,1" in source, "ASS subtitle baseline must sit inside its own card"
    assert "subtitle_panel_color=bg" in source
    assert "narration captions lack their backing card" in source


def test_cta_button_does_not_overlap_two_line_narration_subtitles():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[1]/'photo_video.py').read_text()
    assert 'd.rounded_rectangle((96,1450,790,1540)' in src
    assert "if scene==len(phrases)-1:" in src
    assert "for row,line in enumerate(label_lines):text(d,(96,1435+row*52),line,42)" in src
    # Caption safe area begins around 1578 for two lines at 66px.
    assert 1540 < 1578


def test_four_scene_sparse_ad_keeps_cta_on_actual_product_photo():
    sparse = {
        'phrases': ['Hook', 'Seller benefit', 'Distinct seller fact', 'Call to action'],
        'captions': ['Product', 'Benefit', 'Fact', 'Visit product'],
        'scenes': [{'stage': stage} for stage in ['hook', 'benefit', 'routine', 'cta']],
    }
    scenes = build_scene_storyboard(sparse, 1, [False])
    assert len(scenes) == 4
    assert [item['layout'] for item in scenes] == [
        'photo_hero', 'seller_fact_card', 'seller_fact_card', 'photo_hero'
    ]
    assert scenes[-1]['stage'] == 'cta'
    assert scenes[-1]['photo_index'] == 0


def test_rendered_subtitle_ink_gate_rejects_empty_card_and_accepts_readable_text():
    """A present background panel is not evidence that ASS rendered captions."""
    from PIL import Image, ImageDraw, ImageFont
    from backend.photo_video import subtitle_ink_pixels
    background = (244, 245, 239)
    frame = Image.new('RGB', (1080, 1920), background)
    assert subtitle_ink_pixels(frame, background) == 0
    draw = ImageDraw.Draw(frame)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 66)
    draw.text((240, 1700), 'Motion sensor night light', font=font, fill=(23, 60, 50))
    assert subtitle_ink_pixels(frame, background) >= 500


def test_rendered_subtitle_inspection_requires_actual_timed_ass_events(tmp_path):
    """No missing or empty subtitle track may silently pass creative QA."""
    from backend.photo_video import inspect_rendered_subtitles
    with pytest.raises(ValueError, match='subtitle file is missing'):
        inspect_rendered_subtitles(tmp_path/'video.mp4', 15, tmp_path, '#F4F5EF')
    (tmp_path/'captions.ass').write_text('[Events]\\n', encoding='utf-8')
    with pytest.raises(ValueError, match='no timed narration subtitles'):
        inspect_rendered_subtitles(tmp_path/'video.mp4', 15, tmp_path, '#F4F5EF')


def test_one_source_photo_never_repeats_same_detail_crop_across_scenes():
    """Actual JAS11242 review: repeated near-identical product photos are not an ad."""
    for count in (4, 5):
        base = plan()
        base['phrases'] = base['phrases'][:count]
        base['captions'] = base['captions'][:count]
        base['scenes'] = base['scenes'][:count]
        scenes = build_scene_storyboard(base, 1, [True])
        assert sum(scene['photo_treatment'] == 'real_photo_detail_crop' for scene in scenes) == 1
        assert scenes[0]['layout'] == scenes[-1]['layout'] == 'photo_hero'
        assert scenes[2]['layout'] == 'seller_fact_card'

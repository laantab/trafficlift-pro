"""Phase 2 video storyboard: real-photo-only scenes and reliable metadata."""
import pytest
from backend.photo_video import build_scene_storyboard, balanced_caption_chunks


def plan():
    return {
        "phrases": ["The hook", "The product", "Seller-supported benefit", "Compare details", "Check the listing"],
        "captions": ["Hook", "Product", "Benefit", "Details", "Link"],
        "scenes": [{"stage": stage} for stage in ["hook", "product", "benefit", "buying_details", "cta"]],
    }


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
    assert "if scene == 0:" in src
    assert "scene_heading =" in src
    assert "labels=storyboard[scene]['caption']" in src

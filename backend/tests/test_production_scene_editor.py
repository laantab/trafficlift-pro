"""Video Editor-in-Chief v3.0.9: internal media review and protected film edit tests.

All tests use locally generated fixtures. No HeyGen POST or provider credits.
"""
import subprocess
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from backend.production_scene_editor import inspect_footage, cut_unusable_scene


@pytest.fixture
def voiced_video(tmp_path):
    from backend.social_exports_api import _ffmpeg
    source = tmp_path / "original.mp4"
    subprocess.run([
        _ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x240:rate=12",
        "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=16000",
        "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True, timeout=35)
    return source


def test_editor_samples_actual_video_and_reports_not_approved(voiced_video):
    result = inspect_footage(voiced_video)
    assert 7.5 <= result["duration"] <= 8.5
    assert result["width"] == 160 and result["height"] == 240
    assert result["has_audio"] is True
    assert len(result["frames"]) >= 5
    assert all(frame["jpeg_base64"].startswith("/9j/") for frame in result["frames"])
    assert result["approval"] == "NOT APPROVED"
    assert "not automatically certified" in result["scene_identity_analysis"]
    assert result["provider_credits_used"] is False


def test_real_editor_removes_bad_scene_and_synced_narration(voiced_video, tmp_path):
    output = tmp_path / "corrected.mp4"
    info = cut_unusable_scene(voiced_video, output, 2.0, 4.0)
    assert output.is_file() and output.stat().st_size > 2048
    assert abs(info["before"] - info["after"] - 2) < .5
    assert info["removed"] == [2.0, 4.0]
    reviewed = inspect_footage(output)
    assert reviewed["has_audio"] is True
    assert reviewed["approval"] == "NOT APPROVED"  # editing is not publishing permission


@pytest.mark.parametrize("start,end", [
    (0, 7), (1, 1.2), (-1, 3), (3, 99), ("NaN", 3), (3, float("inf")),
])
def test_editor_refuses_unsafe_cuts_without_destroying_original(voiced_video, tmp_path, start, end):
    output = tmp_path / "not-approved.mp4"
    with pytest.raises(HTTPException) as error:
        cut_unusable_scene(voiced_video, output, start, end)
    assert error.value.status_code == 422
    assert voiced_video.exists()
    assert not output.exists()


def test_scene_review_endpoint_requires_owner_before_provider_download():
    from backend.production_scene_editor import review_heygen
    with patch("backend.production_scene_editor.video_guard",
               side_effect=HTTPException(401, "Owner only")), \
         patch("backend.production_scene_editor._source_from_heygen") as source:
        with pytest.raises(HTTPException) as error:
            review_heygen("sample_video_123", Mock())
    assert error.value.status_code == 401
    source.assert_not_called()


def test_corrected_scene_export_requires_both_start_and_end():
    from backend.social_exports_api import ExportOptions
    option = ExportOptions(marketing_url="https://example.com/product", remove_start=3)
    assert option.remove_start == 3 and option.remove_end is None


def test_editor_is_integrated_with_real_heygen_export_and_video_studio():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    exports=(root/"backend/social_exports_api.py").read_text()
    html=(root/"index.html").read_text()
    assert "cut_unusable_scene(source, corrected, options.remove_start, options.remove_end)" in exports
    assert "'/api/v1/video-editor-in-chief/heygen/'" in html
    assert "void checkFinishedVideoQuality();" in html
    assert "exportsButton.disabled = !identityCheck.checked || !editorReviewed;" in html
    assert "body: JSON.stringify({marketing_url: marketingUrl})" in html
    assert "Editor-in-Chief: inspect scenes" not in html
    assert "reviewPanel.append(" not in html
    assert "Remove rejected scene from exports" not in html


def test_edited_preview_requires_owner_before_downloading_provider_video():
    from backend.production_scene_editor import preview_cut, CutRequest
    with patch("backend.production_scene_editor.video_guard",
               side_effect=HTTPException(401, "Owner only")), \
         patch("backend.production_scene_editor._source_from_heygen") as source:
        with pytest.raises(HTTPException) as error:
            preview_cut("sample_video_123", CutRequest(remove_start=2, remove_end=4), Mock())
    assert error.value.status_code == 401
    source.assert_not_called()


def test_corrected_preview_remains_owner_only_not_part_of_customer_video_screen():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    html=(root/"index.html").read_text()
    editor=(root/"backend/production_scene_editor.py").read_text()
    assert 'def preview_cut(' in editor
    assert 'video_guard(request)' in editor
    assert '/preview-cut' in editor
    assert '/preview-cut' not in html
    assert "Bad scene starts (s)" not in html
    assert "Editor-in-Chief: inspect scenes" not in html
    assert "Checking video quality…" in html
    assert "void checkFinishedVideoQuality();" in html


def test_thermoflask_storyboard_prevents_secondary_cup_pouring():
    from backend.heygen_video_api import product_prompt
    prompt=product_prompt(
        "ThermoFlask Insulated Stainless Steel Coffee Travel Mug with Sip-N-Lok Lid",
        {"mode":"product_overview","evidence":[]},
    )
    assert "ONLY drinking vessel" in prompt
    assert "Never show coffee being poured OUT OF another cup" in prompt
    assert "OMIT" in prompt and "pouring scene" in prompt
    assert "exact original mug" in prompt

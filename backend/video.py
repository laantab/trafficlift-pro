"""
trafficlift_pro/backend/video.py
MiniMax Video Rendering Client

Submits text-to-video tasks to MiniMax (H3 / H3-Max / Hailuo-2.3) and polls
until the final MP4 is ready. Designed to plug into the TrafficLift Pro
backend as a new "render an actual short" capability on top of the existing
"generate video params" helper.

End-to-end flow:
    client.submit(prompt, duration, ratio, resolution) → task_id
    client.query(task_id)                                → status + video_url
    client.render(prompt, ...)                           → blocks until ready,
                                                           returns video_url

Environment variables:
    MINIMAX_API_KEY    — MiniMax API key (required for actual rendering)
    MINIMAX_BASE_URL   — override the API base (default: https://api.minimax.chat/v1)
    MINIMAX_POLL_INTERVAL — seconds between status checks (default: 10)
    MINIMAX_POLL_TIMEOUT  — total seconds to wait before giving up (default: 2400)
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class VideoTask:
    """In-flight video generation task."""
    task_id: str
    model: str
    status: str = "submitted"           # submitted | queued | running | succeeded | failed | cancelled
    video_url: Optional[str] = None
    failure_reason: Optional[str] = None
    duration_seconds: Optional[int] = None
    resolution: Optional[str] = None
    ratio: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "model": self.model,
            "status": self.status,
            "video_url": self.video_url,
            "failure_reason": self.failure_reason,
            "duration_seconds": self.duration_seconds,
            "resolution": self.resolution,
            "ratio": self.ratio,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────


class MiniMaxVideoError(RuntimeError):
    """Raised when MiniMax returns a terminal failure or the timeout elapses."""


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────


class MiniMaxVideoClient:
    """
    Synchronous MiniMax video-rendering client.

    Submit + poll in one shot via `render()`. Use `submit()` + `query()` for
    async / frontend-polling patterns.
    """

    BASE_URL = "https://api.minimax.chat/v1"

    SUPPORTED_MODELS = {
        # model:        (min, max) duration, allowed ratios, allowed resolutions
        "MiniMax-H3":         {"min_dur": 4,  "max_dur": 15,
                                "ratios": ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                                "resolutions": ["768P", "2K"]},
        "MiniMax-H3-Max":     {"min_dur": 5,  "max_dur": 15,
                                "ratios": ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                                "resolutions": ["480P", "768P"]},
        "MiniMax-Hailuo-2.3": {"min_dur": 6,  "max_dur": 10,
                                "ratios": ["adaptive", "16:9"],
                                "resolutions": ["768P", "1080P"]},
    }

    TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "Success", "Fail"}

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[float] = None,
    ):
        self.api_key = (api_key or os.getenv("MINIMAX_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("MINIMAX_BASE_URL", self.BASE_URL)).rstrip("/")
        self.poll_interval = float(
            poll_interval if poll_interval is not None
            else os.getenv("MINIMAX_POLL_INTERVAL", "10")
        )
        self.poll_timeout = float(
            poll_timeout if poll_timeout is not None
            else os.getenv("MINIMAX_POLL_TIMEOUT", "2400")
        )

        if not self.api_key:
            raise MiniMaxVideoError(
                "MINIMAX_API_KEY is not set. Add it to Render's environment "
                "variables (or your local .env) to enable video rendering."
            )

    # ── Public: config ────────────────────────────────────────────────────────

    @staticmethod
    def is_configured() -> bool:
        return bool(os.getenv("MINIMAX_API_KEY", "").strip())

    @staticmethod
    def supported_models() -> list[str]:
        return list(MiniMaxVideoClient.SUPPORTED_MODELS.keys())

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _validate(self, model: str, duration: int, ratio: str, resolution: str) -> None:
        if model not in self.SUPPORTED_MODELS:
            raise MiniMaxVideoError(
                f"Unsupported model: {model}. Supported: {self.supported_models()}"
            )
        spec = self.SUPPORTED_MODELS[model]
        if not (spec["min_dur"] <= duration <= spec["max_dur"]):
            raise MiniMaxVideoError(
                f"Duration {duration}s out of range for {model}: "
                f"{spec['min_dur']}-{spec['max_dur']}s"
            )
        if ratio not in spec["ratios"]:
            raise MiniMaxVideoError(
                f"Ratio {ratio!r} not supported by {model}. Allowed: {spec['ratios']}"
            )
        if resolution not in spec["resolutions"]:
            raise MiniMaxVideoError(
                f"Resolution {resolution!r} not supported by {model}. "
                f"Allowed: {spec['resolutions']}"
            )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

    # ── Public: submit ────────────────────────────────────────────────────────

    def submit(
        self,
        prompt: str,
        *,
        model: str = "MiniMax-H3",
        duration: int = 15,
        ratio: str = "9:16",
        resolution: str = "768P",
        reference_image_url: Optional[str] = None,
    ) -> VideoTask:
        """
        Submit a text-to-video task. Returns a VideoTask with task_id and
        initial status. The video_url will be populated by `query()` once
        MiniMax reports `succeeded`.
        """
        self._validate(model, duration, ratio, resolution)

        if not (1 <= len(prompt) <= 7000):
            raise MiniMaxVideoError("prompt must be 1-7000 characters")

        payload: dict = {
            "model":      model,
            "prompt":     prompt,
            "duration":   duration,
            "ratio":      ratio,
            "resolution": resolution,
        }
        if reference_image_url:
            payload["input_image"] = {
                "mime_type": "image/png",
                "url":       reference_image_url,
            }

        url = f"{self.base_url}/video_generation"
        logger.info("MiniMax submit model=%s duration=%ss ratio=%s res=%s",
                    model, duration, ratio, resolution)

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise MiniMaxVideoError(
                f"MiniMax submit HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            ) from exc
        except httpx.RequestError as exc:
            raise MiniMaxVideoError(f"MiniMax submit network error: {exc}") from exc

        # The MiniMax response shape varies by model. Accept common keys.
        task_id = (
            data.get("task_id")
            or data.get("video_id")
            or data.get("id")
            or data.get("data", {}).get("task_id")
        )
        if not task_id:
            raise MiniMaxVideoError(f"MiniMax submit returned no task_id: {data}")

        return VideoTask(
            task_id=str(task_id),
            model=model,
            status=data.get("status", "submitted"),
            duration_seconds=duration,
            resolution=resolution,
            ratio=ratio,
        )

    # ── Public: query ─────────────────────────────────────────────────────────

    def query(self, task_id: str, model: Optional[str] = None) -> VideoTask:
        """
        Look up the current status of a previously-submitted task.
        Returns a fresh VideoTask snapshot (status, video_url if done, etc.).
        """
        url = f"{self.base_url}/query/video_generation/{task_id}"
        headers = self._headers()

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise MiniMaxVideoError(
                f"MiniMax query HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            ) from exc
        except httpx.RequestError as exc:
            raise MiniMaxVideoError(f"MiniMax query network error: {exc}") from exc

        status = data.get("status") or "running"
        video_url = (
            data.get("video_url")
            or (data.get("assets", [{}])[0].get("url") if data.get("assets") else None)
            or data.get("file_id")
            or data.get("url")
        )
        failure = data.get("failure_reason") or data.get("error") or data.get("message")

        return VideoTask(
            task_id=str(data.get("task_id") or task_id),
            model=model or data.get("model", "MiniMax-H3"),
            status=status,
            video_url=video_url,
            failure_reason=failure,
            duration_seconds=data.get("duration"),
            resolution=data.get("resolution"),
            ratio=data.get("ratio"),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )

    # ── Public: render (submit + poll until done) ─────────────────────────────

    def render(
        self,
        prompt: str,
        *,
        model: str = "MiniMax-H3",
        duration: int = 15,
        ratio: str = "9:16",
        resolution: str = "768P",
        reference_image_url: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[float] = None,
    ) -> VideoTask:
        """
        Submit a video task and poll until it reaches a terminal status.
        Returns the final VideoTask (success: video_url set; failure:
        failure_reason set + MiniMaxVideoError raised).
        """
        task = self.submit(
            prompt,
            model=model,
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            reference_image_url=reference_image_url,
        )
        return self.wait(task, poll_interval=poll_interval, poll_timeout=poll_timeout)

    def wait(
        self,
        task: VideoTask,
        *,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[float] = None,
    ) -> VideoTask:
        """
        Poll an already-submitted VideoTask until it reaches a terminal status.
        """
        interval = poll_interval if poll_interval is not None else self.poll_interval
        timeout  = poll_timeout  if poll_timeout  is not None else self.poll_timeout

        start = time.monotonic()
        logger.info("Polling MiniMax task %s (interval=%.0fs timeout=%.0fs)",
                    task.task_id, interval, timeout)

        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise MiniMaxVideoError(
                    f"MiniMax task {task.task_id} timed out after {timeout:.0f}s "
                    f"(last status: {task.status})"
                )

            current = self.query(task.task_id, model=task.model)
            task.status        = current.status
            task.video_url     = current.video_url
            task.failure_reason= current.failure_reason
            task.updated_at    = current.updated_at

            logger.info("task=%s status=%s elapsed=%.0fs",
                        task.task_id, task.status, elapsed)

            if task.status in self.TERMINAL_STATUSES or task.status.lower() in {"succeeded", "failed", "cancelled"}:
                break

            time.sleep(interval)

        if task.status.lower() in {"failed", "fail"}:
            raise MiniMaxVideoError(
                f"MiniMax task {task.task_id} failed: {task.failure_reason or 'unknown reason'}"
            )
        if task.status.lower() in {"cancelled", "cancel"}:
            raise MiniMaxVideoError(f"MiniMax task {task.task_id} was cancelled")

        return task


# ─────────────────────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────────────────────


def build_trafficlift_short_prompt(
    product_name: str,
    hook: str,
    benefit: str,
    cta: str,
    *,
    visual_style: str = (
        "sleek, high-contrast neon lighting (cyan + magenta accents on a deep "
        "dark-mode background), dynamic modern product showcase, cinematic "
        "motion, smooth 60fps"
    ),
    voiceover: Optional[str] = None,
    duration: int = 15,
) -> str:
    """
    Build a MiniMax-H3 prompt for a 9:16 TrafficLift Pro short.
    Maps the user's hook → benefit → CTA sequence onto the requested
    Scene 1 (0-3s) / Scene 2 (3-8s) / Scene 3 (8-15s) timing.

    Args:
        product_name:  Name shown in the reveal (Scene 2)
        hook:          Visual/scene description for the attention-grabbing hook
        benefit:       What the product does — shown during the reveal
        cta:           Call to action spoken/shown at the end
        visual_style:  Override the visual style line if you want a different mood
        voiceover:     Optional exact script for the voiceover. If None, a
                       professional placeholder is generated from `cta`.
        duration:      Total target length. Affects scene splits.
    """
    # 0-3 / 3-8 / 8-15 are the canonical scene boundaries the user asked for.
    # If `duration` differs, scale proportionally (capped).
    sec_1 = 3
    sec_2 = 8 if duration >= 8 else max(int(duration * 0.45), sec_1)
    sec_3 = duration

    vo = voiceover or (
        f"Stop guessing your traffic. {product_name} gives you {benefit} in seconds. "
        f"{cta}."
    )

    return (
        f"Cinematic vertical 9:16 product showcase short for \"{product_name}\". "
        f"Visual style: {visual_style}. "
        f"Scene 1 — Hook ({0}-{sec_1}s): {hook}. "
        f"Scene 2 — Product Reveal ({sec_1}-{sec_2}s): {benefit}. "
        f"Scene 3 — Call to Action ({sec_2}-{sec_3}s): {cta}. "
        f"Audio: crisp electronic background beat at 120 BPM with deep sub-bass, "
        f"subtle electronic risers building through Scene 2 and peaking at the CTA. "
        f"A confident professional male voiceover delivers this dialogue in Scene 3: "
        f"\"{vo}\" Final beat lands on the {product_name} logo with a triumphant "
        f"neon flash and a deep sub-bass drop."
    )


def client() -> MiniMaxVideoClient:
    """Lazy singleton accessor — only constructs the client when actually used."""
    return MiniMaxVideoClient()

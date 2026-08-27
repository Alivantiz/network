"""Cutting a long video into vertical TikTok-sized parts with ffmpeg."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ClipConfig
from .errors import VideoError

log = logging.getLogger("yt2tt.video")


@dataclass(frozen=True)
class Segment:
    index: int  # 1-based
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def plan_segments(
    total_duration: float,
    *,
    part_seconds: int,
    min_part_seconds: int = 0,
    skip_intro_seconds: int = 0,
    skip_outro_seconds: int = 0,
    max_parts: int | None = None,
) -> list[Segment]:
    """Split a timeline into consecutive parts.

    A trailing remainder shorter than *min_part_seconds* is merged into the
    previous part instead of being posted as a stub — unless it is the only
    part, in which case it is kept as-is.
    """
    if part_seconds <= 0:
        raise ValueError("part_seconds must be > 0")

    start = max(0.0, float(skip_intro_seconds))
    end = float(total_duration) - max(0.0, float(skip_outro_seconds))
    if end - start <= 0:
        return []

    bounds: list[tuple[float, float]] = []
    cursor = start
    while cursor < end - 1e-6:
        duration = min(float(part_seconds), end - cursor)
        bounds.append((cursor, duration))
        cursor += duration

    if len(bounds) > 1 and bounds[-1][1] < min_part_seconds:
        prev_start, prev_duration = bounds[-2]
        bounds[-2] = (prev_start, prev_duration + bounds[-1][1])
        bounds.pop()

    if max_parts is not None:
        bounds = bounds[:max_parts]

    return [Segment(i, s, d) for i, (s, d) in enumerate(bounds, start=1)]


def build_vfilter(cfg: ClipConfig) -> str | None:
    """Build the ffmpeg -vf chain that fits the source into a vertical frame."""
    w, h = cfg.width, cfg.height
    if cfg.orientation == "none":
        return None
    if cfg.orientation == "crop":
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1"
        )
    if cfg.orientation == "pad":
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
    # blur: source centred over a blurred, zoom-filled copy of itself
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"boxblur=luma_radius=40:luma_power=2[bgb];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


class Cutter:
    def __init__(self, cfg: ClipConfig) -> None:
        self.cfg = cfg
        self.dir = Path(cfg.dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ensure_available() -> None:
        for tool in ("ffmpeg", "ffprobe"):
            if shutil.which(tool) is None:
                raise VideoError(f"{tool} not found in PATH — install ffmpeg")

    @staticmethod
    def probe_duration(path: Path) -> float:
        Cutter.ensure_available()
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise VideoError(f"ffprobe failed for {path}: {proc.stderr.strip()[:300]}")
        try:
            return float(json.loads(proc.stdout)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise VideoError(f"could not read duration of {path}") from exc

    def clip_path(self, video_id: str, index: int) -> Path:
        return self.dir / f"{video_id}_part{index:03d}.mp4"

    def cut(
        self, source: Path, video_id: str, segment: Segment, *, overwrite: bool = False
    ) -> Path:
        """Render one segment to a vertical mp4 and return its path."""
        self.ensure_available()
        out = self.clip_path(video_id, segment.index)
        if out.exists() and out.stat().st_size > 0 and not overwrite:
            log.info("clip already rendered: %s", out.name)
            return out

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{segment.start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{segment.duration:.3f}",
        ]
        vfilter = build_vfilter(self.cfg)
        if vfilter:
            cmd += ["-filter_complex", vfilter] if "[bg]" in vfilter else ["-vf", vfilter]
        cmd += [
            "-r",
            str(self.cfg.fps),
            "-c:v",
            "libx264",
            "-preset",
            self.cfg.preset,
            "-crf",
            str(self.cfg.crf),
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-c:a",
            "aac",
            "-b:a",
            self.cfg.audio_bitrate,
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(out),
        ]
        log.info("cutting part %d (%.0fs @ %.0fs)", segment.index, segment.duration, segment.start)
        log.debug("running: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise VideoError(
                f"ffmpeg failed on part {segment.index} of {video_id}: "
                f"{proc.stderr.strip()[:800]}"
            )
        if not out.exists() or out.stat().st_size == 0:
            raise VideoError(f"ffmpeg produced no output for part {segment.index}")
        return out

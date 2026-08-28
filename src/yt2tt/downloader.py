"""Downloading source videos with yt-dlp."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from .config import DownloadConfig
from .errors import DownloadError
from .tools import require_tool

log = logging.getLogger("yt2tt.download")


class Downloader:
    def __init__(self, cfg: DownloadConfig) -> None:
        self.cfg = cfg
        self.dir = Path(cfg.dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ensure_available() -> str:
        """Return the yt-dlp binary to run, or raise if it is not installed."""
        return require_tool("yt-dlp", DownloadError, "pip install -r requirements.txt")

    def existing(self, video_id: str) -> Path | None:
        for candidate in sorted(self.dir.glob(f"{video_id}.*")):
            if candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}:
                return candidate
        return None

    def download(self, url: str, video_id: str) -> Path:
        """Fetch *url* into the download dir and return the local file path."""
        cached = self.existing(video_id)
        if cached:
            log.info("using cached download %s", cached.name)
            return cached

        binary = self.ensure_available()
        output = str(self.dir / "%(id)s.%(ext)s")
        cmd = [
            binary,
            "-f",
            self.cfg.format,
            "--merge-output-format",
            "mp4",
            "--no-playlist",
            "--no-progress",
            "--newline",
            "--retries",
            str(self.cfg.retries),
            "-o",
            output,
        ]
        if self.cfg.cookies_file:
            cmd += ["--cookies", self.cfg.cookies_file]
        if self.cfg.rate_limit:
            cmd += ["--limit-rate", self.cfg.rate_limit]
        if self.cfg.max_filesize_mb:
            cmd += ["--max-filesize", f"{self.cfg.max_filesize_mb}M"]
        cmd.append(url)

        log.info("downloading %s", url)
        log.debug("running: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise DownloadError(f"yt-dlp failed for {url}: {proc.stderr.strip()[:800]}")

        path = self.existing(video_id)
        if path is None:
            raise DownloadError(f"download finished but no file found for {video_id}")
        log.info("downloaded %s (%.1f MB)", path.name, path.stat().st_size / 1_048_576)
        return path

    def metadata(self, url: str) -> dict:
        """Fetch yt-dlp metadata for a single URL without downloading it."""
        binary = self.ensure_available()
        proc = subprocess.run(
            [binary, "--dump-json", "--no-playlist", "--no-warnings", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise DownloadError(f"yt-dlp metadata failed for {url}: {proc.stderr.strip()[:500]}")
        try:
            return json.loads(proc.stdout.splitlines()[0])
        except (json.JSONDecodeError, IndexError) as exc:
            raise DownloadError(f"unparseable yt-dlp metadata for {url}") from exc

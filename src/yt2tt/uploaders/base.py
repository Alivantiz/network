"""Uploader interface plus a no-network implementation for dry runs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger("yt2tt.upload")


@dataclass
class UploadResult:
    publish_id: str
    status: str
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "DRY_RUN"}


class Uploader(Protocol):
    def upload(self, path: Path, caption: str) -> UploadResult: ...


class DryRunUploader:
    """Logs what would have been posted and returns a fake publish id."""

    def __init__(self) -> None:
        self._counter = 0

    def upload(self, path: Path, caption: str) -> UploadResult:
        self._counter += 1
        size_mb = path.stat().st_size / 1_048_576 if path.exists() else 0.0
        log.info("[dry-run] would upload %s (%.1f MB) — %s", path.name, size_mb, caption)
        return UploadResult(publish_id=f"dryrun-{self._counter}", status="DRY_RUN")

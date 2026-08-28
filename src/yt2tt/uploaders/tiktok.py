"""TikTok Content Posting API v2 client.

Two publishing modes:

* ``inbox``  — the video lands in the creator's TikTok inbox as a draft; they
               finish and publish it in the app. Works for unaudited apps and
               is the safe default.
* ``direct`` — publishes straight to the account. Requires the
               ``video.publish`` scope and an audited app, and the caller must
               honour the creator's privacy options returned by
               ``creator_info``.

Docs: https://developers.tiktok.com/doc/content-posting-api-get-started
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from ..config import TikTokConfig
from ..errors import AuthError, UploadError
from .base import UploadResult

log = logging.getLogger("yt2tt.tiktok")

API_ROOT = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API_ROOT}/oauth/token/"
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
TERMINAL_STATUSES = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"}


def plan_chunks(video_size: int, chunk_size: int) -> tuple[int, int, list[tuple[int, int]]]:
    """Split a file into TikTok-compliant byte ranges.

    Returns ``(chunk_size, total_chunk_count, [(start, end_inclusive), ...])``.
    A file smaller than 5 MB must be sent whole; the final chunk absorbs the
    remainder rather than being sent as an undersized chunk.
    """
    if video_size <= 0:
        raise ValueError("video_size must be > 0")
    if video_size < MIN_CHUNK:
        return video_size, 1, [(0, video_size - 1)]

    chunk_size = max(MIN_CHUNK, min(int(chunk_size), MAX_CHUNK, video_size))
    total = video_size // chunk_size
    ranges: list[tuple[int, int]] = []
    for i in range(total):
        start = i * chunk_size
        end = video_size - 1 if i == total - 1 else start + chunk_size - 1
        ranges.append((start, end))
    return chunk_size, total, ranges


class TikTokUploader:
    def __init__(self, cfg: TikTokConfig, *, token_cache: Path | None = None) -> None:
        self.cfg = cfg
        self.token_cache = Path(token_cache) if token_cache else None
        self._access_token: str | None = cfg.access_token
        self._expires_at: float = float("inf") if cfg.access_token else 0.0
        self._session = None
        self._load_cached_token()

    # ---- plumbing -----------------------------------------------------
    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _load_cached_token(self) -> None:
        if not self.token_cache or not self.token_cache.is_file():
            return
        try:
            data = json.loads(self.token_cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if data.get("access_token") and float(data.get("expires_at", 0)) > time.time() + 60:
            self._access_token = data["access_token"]
            self._expires_at = float(data["expires_at"])
            log.debug("reusing cached TikTok access token")
        if data.get("refresh_token"):
            self.cfg.refresh_token = data["refresh_token"]

    def _save_cached_token(self) -> None:
        if not self.token_cache:
            return
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        self.token_cache.write_text(
            json.dumps(
                {
                    "access_token": self._access_token,
                    "refresh_token": self.cfg.refresh_token,
                    "expires_at": self._expires_at,
                }
            ),
            encoding="utf-8",
        )
        try:
            self.token_cache.chmod(0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass

    def access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        if not self.cfg.refresh_token:
            if self._access_token:
                return self._access_token
            raise AuthError("no TikTok access token and no refresh token to obtain one")
        return self._refresh()

    def _refresh(self) -> str:
        log.info("refreshing TikTok access token")
        resp = self.session.post(
            TOKEN_URL,
            data={
                "client_key": self.cfg.client_key,
                "client_secret": self.cfg.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.cfg.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        payload = _json_or_raise(resp, "token refresh")
        if "access_token" not in payload:
            raise AuthError(f"token refresh failed: {payload}")
        self._access_token = payload["access_token"]
        self._expires_at = time.time() + float(payload.get("expires_in", 3600))
        if payload.get("refresh_token"):
            self.cfg.refresh_token = payload["refresh_token"]
        self._save_cached_token()
        return self._access_token

    def _post(self, path: str, body: dict) -> dict:
        resp = self.session.post(
            f"{API_ROOT}{path}",
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=body,
            timeout=60,
        )
        payload = _json_or_raise(resp, path)
        error = payload.get("error") or {}
        if error.get("code") not in (None, "ok"):
            if error.get("code") in {"access_token_invalid", "scope_not_authorized"}:
                raise AuthError(f"TikTok {path}: {error}")
            raise UploadError(f"TikTok {path} error: {error}")
        return payload.get("data", {})

    # ---- API ----------------------------------------------------------
    def creator_info(self) -> dict:
        """Query posting options for the authorised creator (required for direct post)."""
        return self._post("/post/publish/creator_info/query/", {})

    def _init_upload(self, size: int, caption: str) -> dict:
        chunk_size, total_chunks, _ = plan_chunks(size, self.cfg.chunk_size_mb * 1024 * 1024)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        }
        if self.cfg.mode == "inbox":
            return self._post(
                "/post/publish/inbox/video/init/", {"source_info": source_info}
            )
        return self._post(
            "/post/publish/video/init/",
            {
                "post_info": {
                    "title": caption,
                    "privacy_level": self.cfg.privacy_level,
                    "disable_comment": self.cfg.disable_comment,
                    "disable_duet": self.cfg.disable_duet,
                    "disable_stitch": self.cfg.disable_stitch,
                    "video_cover_timestamp_ms": self.cfg.video_cover_timestamp_ms,
                },
                "source_info": source_info,
            },
        )

    def _put_chunks(self, upload_url: str, path: Path, size: int) -> None:
        _, _, ranges = plan_chunks(size, self.cfg.chunk_size_mb * 1024 * 1024)
        with path.open("rb") as fh:
            for i, (start, end) in enumerate(ranges, start=1):
                fh.seek(start)
                blob = fh.read(end - start + 1)
                log.debug("uploading chunk %d/%d (%d bytes)", i, len(ranges), len(blob))
                resp = self.session.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(blob)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    },
                    data=blob,
                    timeout=600,
                )
                if resp.status_code not in (200, 201, 206):
                    raise UploadError(
                        f"chunk {i}/{len(ranges)} rejected [{resp.status_code}]: "
                        f"{resp.text[:300]}"
                    )

    def poll_status(self, publish_id: str, timeout: int | None = None) -> UploadResult:
        deadline = time.time() + (timeout if timeout is not None else self.cfg.poll_timeout_seconds)
        delay = 3.0
        last = "PROCESSING_UPLOAD"
        while time.time() < deadline:
            data = self._post("/post/publish/status/fetch/", {"publish_id": publish_id})
            last = data.get("status", last)
            if last in TERMINAL_STATUSES:
                post_ids = data.get("publicaly_available_post_id") or [None]
                detail = data.get("fail_reason") or post_ids[0]
                if last == "FAILED":
                    raise UploadError(f"TikTok publish failed: {data}")
                return UploadResult(publish_id=publish_id, status=last, detail=str(detail))
            time.sleep(delay)
            delay = min(delay * 1.5, 20.0)
        return UploadResult(publish_id=publish_id, status=last, detail="timed out while polling")

    def upload(
        self,
        path: Path,
        caption: str,
        *,
        on_publish_id: Callable[[str], None] | None = None,
    ) -> UploadResult:
        size = path.stat().st_size
        if size == 0:
            raise UploadError(f"refusing to upload empty file: {path}")
        log.info("uploading %s (%.1f MB) in %s mode", path.name, size / 1_048_576, self.cfg.mode)

        data = self._init_upload(size, caption)
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise UploadError(f"init returned no upload target: {data}")
        # Hand the id over before a single byte moves: from here on the post may
        # exist on TikTok's side even if we never see the result.
        if on_publish_id is not None:
            on_publish_id(publish_id)

        self._put_chunks(upload_url, path, size)
        result = self.poll_status(publish_id)
        log.info("upload %s -> %s", path.name, result.status)
        return result


def _json_or_raise(resp, what: str) -> dict:
    if resp.status_code == 401:
        raise AuthError(f"TikTok {what}: unauthorised (401) — token expired or wrong scope")
    try:
        return resp.json()
    except ValueError as exc:
        raise UploadError(
            f"TikTok {what}: non-JSON reply [{resp.status_code}] {resp.text[:300]}"
        ) from exc

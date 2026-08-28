"""Stage orchestration: discover -> download -> cut -> upload."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import Config
from .downloader import Downloader
from .errors import Yt2TtError
from .metadata import build_caption
from .state import ClipRow, Store
from .uploaders.base import DryRunUploader, Uploader, UploadResult
from .uploaders.tiktok import TikTokUploader
from .video import Cutter, plan_segments
from .youtube import YouTubeSearcher

log = logging.getLogger("yt2tt.pipeline")


def build_uploader(cfg: Config) -> Uploader:
    if cfg.runtime.dry_run:
        return DryRunUploader()
    cfg.require_tiktok_credentials()
    cache = Path(cfg.runtime.state_db).with_name(".tiktok_token.json")
    return TikTokUploader(cfg.tiktok, token_cache=cache)


class _Handover:
    """Records the publish id a clip was given, the moment the remote hands it out.

    Once TikTok has issued an id the post may exist on their side, so the clip
    must stay resumable instead of being marked failed and re-sent later.
    """

    def __init__(self, store: Store, clip_id: int) -> None:
        self._store = store
        self._clip_id = clip_id
        self.publish_id: str | None = None

    def __call__(self, publish_id: str) -> None:
        self.publish_id = publish_id
        self._store.set_clip_status(self._clip_id, "uploading", publish_id=publish_id)


class Pipeline:
    def __init__(self, cfg: Config, store: Store, uploader: Uploader | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.downloader = Downloader(cfg.download)
        self.cutter = Cutter(cfg.clip)
        self._uploader = uploader

    @property
    def uploader(self) -> Uploader:
        if self._uploader is None:
            self._uploader = build_uploader(self.cfg)
        return self._uploader

    # ---- stage 1: discovery -------------------------------------------
    def discover(self) -> int:
        searcher = YouTubeSearcher(self.cfg.search)
        added = 0
        for candidate in searcher.search():
            if self.store.has_video(candidate.video_id):
                continue
            if self.store.add_video(candidate.as_record()):
                added += 1
                log.info("queued %s — %s", candidate.video_id, candidate.title)
        log.info("discovery added %d new videos", added)
        return added

    def add_url(self, url: str) -> str:
        """Queue a single video by URL (bypasses search)."""
        meta = self.downloader.metadata(url)
        video_id = meta["id"]
        record = {
            "video_id": video_id,
            "title": meta.get("title") or video_id,
            "url": meta.get("webpage_url") or url,
            "channel": meta.get("channel") or meta.get("uploader"),
            "duration": int(meta["duration"]) if meta.get("duration") else None,
            "published_at": meta.get("upload_date"),
        }
        if self.store.add_video(record):
            log.info("queued %s — %s", video_id, record["title"])
        else:
            log.info("%s is already in the queue", video_id)
        return video_id

    # ---- stage 2+3: fetch and cut --------------------------------------
    def prepare(self, limit: int | None = None) -> int:
        limit = limit if limit is not None else self.cfg.runtime.max_videos_per_run
        videos = self.store.videos_with_status("discovered", "downloaded", limit=limit)
        prepared = 0
        for row in videos:
            try:
                self._prepare_one(row["video_id"], row["url"], row["title"])
                prepared += 1
            except Yt2TtError as exc:
                log.error("failed to prepare %s: %s", row["video_id"], exc)
                self.store.set_video_status(row["video_id"], "failed", note=str(exc)[:500])
        return prepared

    def _prepare_one(self, video_id: str, url: str, title: str) -> None:
        source = self.downloader.download(url, video_id)
        self.store.set_video_status(video_id, "downloaded", source_path=str(source))

        duration = self.cutter.probe_duration(source)
        segments = plan_segments(
            duration,
            part_seconds=self.cfg.clip.part_seconds,
            min_part_seconds=self.cfg.clip.min_part_seconds,
            skip_intro_seconds=self.cfg.clip.skip_intro_seconds,
            skip_outro_seconds=self.cfg.clip.skip_outro_seconds,
            max_parts=self.cfg.clip.max_parts,
        )
        if not segments:
            self.store.set_video_status(
                video_id, "skipped", note="nothing left after intro/outro trim"
            )
            log.warning("%s produced no segments", video_id)
            return

        total = len(segments)
        records = []
        for segment in segments:
            path = self.cutter.cut(source, video_id, segment)
            records.append(
                {
                    "idx": segment.index,
                    "total": total,
                    "path": path,
                    "start": segment.start,
                    "duration": segment.duration,
                    "title": build_caption(title, segment.index, total, self.cfg.metadata),
                }
            )
        self.store.add_clips(video_id, records)
        self.store.set_video_status(video_id, "split")
        log.info("%s cut into %d parts", video_id, total)

        if not self.cfg.runtime.keep_source:
            source.unlink(missing_ok=True)
            log.debug("removed source %s", source)

    # ---- stage 4: upload ------------------------------------------------
    def upload_pending(self, limit: int | None = None) -> int:
        """Post pending parts. A dry run reports but never records anything."""
        dry = self.cfg.runtime.dry_run
        clips = self.store.pending_clips(limit=limit)
        if not clips:
            log.info("nothing pending to upload")
            return 0

        # Build the uploader up front: a missing credential is a problem with the
        # run, not with any single clip, and must not mark the whole queue failed.
        uploader = self.uploader

        posted = 0
        for clip in clips:
            # A clip left in 'uploading' with a publish id was already handed to
            # TikTok by a run that died before it saw the verdict. Ask for the
            # verdict instead of posting the same part twice.
            if not dry and clip.status == "uploading" and clip.publish_id:
                if self._resume(clip):
                    posted += 1
                continue

            # Dry-run posts never reach the database, so count them separately.
            if not self._quota_allows(extra=posted if dry else 0):
                log.warning("daily upload limit (%d) reached", self.cfg.tiktok.daily_limit)
                break
            self._wait_for_interval()

            path = Path(clip.path)
            if not path.is_file():
                msg = f"clip file missing: {path}"
                log.error(msg)
                if not dry:
                    self.store.set_clip_status(clip.id, "failed", error=msg)
                continue

            handover = _Handover(self.store, clip.id)
            if not dry:
                self.store.set_clip_status(clip.id, "uploading")
            try:
                result = uploader.upload(path, clip.title, on_publish_id=None if dry else handover)
            except Yt2TtError as exc:
                log.error("upload failed for %s: %s", path.name, exc)
                if dry:
                    continue
                if handover.publish_id:
                    log.warning(
                        "%s stays open: publish %s already exists and is resolved on the next run",
                        path.name,
                        handover.publish_id,
                    )
                    self.store.set_clip_status(clip.id, "uploading", error=str(exc)[:500])
                else:
                    self.store.set_clip_status(clip.id, "failed", error=str(exc)[:500])
                continue

            if dry:
                posted += 1
            elif self._settle(clip, result):
                posted += 1

        if dry:
            log.info("dry run: %d clip(s) would have been posted", posted)
        else:
            log.info("uploaded %d clip(s)", posted)
        return posted

    def _resume(self, clip: ClipRow) -> bool:
        """Resolve a clip that was already sent but whose result we never saw."""
        log.info("clip %s was already sent as %s — checking its status", clip.id, clip.publish_id)
        try:
            result = self.uploader.poll_status(clip.publish_id)
        except Yt2TtError as exc:
            log.error("could not resolve publish %s: %s", clip.publish_id, exc)
            self.store.set_clip_status(clip.id, "failed", error=str(exc)[:500])
            return False
        return self._settle(clip, result)

    def _settle(self, clip: ClipRow, result: UploadResult) -> bool:
        """Record the outcome of one upload. Returns True when it was posted."""
        if not result.ok:
            self.store.set_clip_status(
                clip.id, "failed", publish_id=result.publish_id, error=result.status
            )
            return False

        self.store.set_clip_status(clip.id, "uploaded", publish_id=result.publish_id)
        if not self.cfg.runtime.keep_clips:
            Path(clip.path).unlink(missing_ok=True)
        if self.store.video_is_complete(clip.video_id):
            self.store.set_video_status(clip.video_id, "done")
            log.info("all parts of %s are posted", clip.video_id)
        return True

    def _quota_allows(self, extra: int = 0) -> bool:
        if self.cfg.tiktok.daily_limit <= 0:
            return True
        uploaded_today = self.store.uploads_since(time.time() - 86400)
        return uploaded_today + extra < self.cfg.tiktok.daily_limit

    def _wait_for_interval(self) -> None:
        interval = self.cfg.tiktok.post_interval_seconds
        if interval <= 0 or self.cfg.runtime.dry_run:
            return
        last = self.store.last_upload_ts()
        if last is None:
            return
        wait = last + interval - time.time()
        if wait > 0:
            log.info("waiting %.0fs before the next post (spacing)", wait)
            time.sleep(wait)

    # ---- full run --------------------------------------------------------
    def run(
        self, *, skip_discovery: bool = False, upload_limit: int | None = None
    ) -> dict[str, int]:
        stats = {"discovered": 0, "prepared": 0, "uploaded": 0}
        if not skip_discovery:
            stats["discovered"] = self.discover()
        stats["prepared"] = self.prepare()
        stats["uploaded"] = self.upload_pending(limit=upload_limit)
        return stats

"""End-to-end pipeline test with the external tools stubbed out."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt2tt.config import Config
from yt2tt.pipeline import Pipeline
from yt2tt.state import Store
from yt2tt.uploaders.base import DryRunUploader, UploadResult
from yt2tt.video import Segment


class FakeUploader:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.uploaded: list[tuple[str, str]] = []
        self.fail_on = fail_on or set()

    def upload(self, path: Path, caption: str) -> UploadResult:
        self.uploaded.append((path.name, caption))
        if path.name in self.fail_on:
            return UploadResult(publish_id="x", status="FAILED")
        return UploadResult(publish_id=f"pub-{len(self.uploaded)}", status="SEND_TO_USER_INBOX")


@pytest.fixture
def cfg(tmp_path):
    config = Config()
    config.runtime.state_db = str(tmp_path / "state.db")
    config.runtime.log_file = None
    config.download.dir = str(tmp_path / "downloads")
    config.clip.dir = str(tmp_path / "clips")
    config.clip.part_seconds = 60
    config.clip.max_parts = 3
    config.tiktok.post_interval_seconds = 0
    config.tiktok.daily_limit = 100
    return config


@pytest.fixture
def stubbed(monkeypatch, tmp_path, cfg):
    """Replace yt-dlp and ffmpeg with local file fakes."""
    source = Path(cfg.download.dir) / "vid1.mp4"

    def fake_download(self, url, video_id):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"source-bytes")
        return source

    def fake_cut(self, src, video_id, segment: Segment, overwrite: bool = False):
        out = self.clip_path(video_id, segment.index)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"clip-bytes")
        return out

    monkeypatch.setattr("yt2tt.downloader.Downloader.download", fake_download)
    monkeypatch.setattr("yt2tt.video.Cutter.cut", fake_cut)
    monkeypatch.setattr("yt2tt.video.Cutter.probe_duration", staticmethod(lambda path: 185.0))
    return source


def queue_video(store: Store) -> None:
    store.add_video(
        {
            "video_id": "vid1",
            "title": "Дорама 1 серия",
            "url": "https://youtu.be/vid1",
            "duration": 185,
        }
    )


def test_prepare_cuts_and_records_clips(cfg, stubbed):
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        Pipeline(cfg, store, uploader=FakeUploader()).prepare()

        clips = store.clips_for_video("vid1")
        assert [c.idx for c in clips] == [1, 2, 3]
        assert clips[-1].duration == pytest.approx(65.0)  # short tail merged
        assert all(Path(c.path).is_file() for c in clips)
        assert store.get_video("vid1")["status"] == "split"
        assert "часть 1/3" in clips[0].title


def test_source_is_removed_unless_kept(cfg, stubbed):
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        Pipeline(cfg, store, uploader=FakeUploader()).prepare()
    assert not stubbed.exists()


def test_prepare_is_idempotent(cfg, stubbed):
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        pipeline = Pipeline(cfg, store, uploader=FakeUploader())
        pipeline.prepare()
        pipeline.prepare()  # video is already 'split', nothing new
        assert len(store.clips_for_video("vid1")) == 3


def test_upload_marks_clips_and_completes_video(cfg, stubbed):
    uploader = FakeUploader()
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        pipeline = Pipeline(cfg, store, uploader=uploader)
        pipeline.prepare()
        assert pipeline.upload_pending() == 3
        assert len(uploader.uploaded) == 3
        assert store.pending_clips() == []
        assert store.get_video("vid1")["status"] == "done"


def test_failed_upload_is_recorded_and_video_stays_open(cfg, stubbed):
    uploader = FakeUploader(fail_on={"vid1_part002.mp4"})
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        pipeline = Pipeline(cfg, store, uploader=uploader)
        pipeline.prepare()
        assert pipeline.upload_pending() == 2
        assert store.summary()["clips"] == {"uploaded": 2, "failed": 1}
        assert store.get_video("vid1")["status"] != "done"


def test_daily_limit_stops_uploading(cfg, stubbed):
    cfg.tiktok.daily_limit = 2
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        pipeline = Pipeline(cfg, store, uploader=FakeUploader())
        pipeline.prepare()
        assert pipeline.upload_pending() == 2
        assert len(store.pending_clips()) == 1


def test_missing_clip_file_fails_gracefully(cfg, stubbed):
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        pipeline = Pipeline(cfg, store, uploader=FakeUploader())
        pipeline.prepare()
        Path(store.clips_for_video("vid1")[0].path).unlink()
        assert pipeline.upload_pending() == 2
        assert store.summary()["clips"]["failed"] == 1


def test_dry_run_uploader_touches_nothing(cfg, stubbed):
    cfg.runtime.dry_run = True
    with Store(cfg.runtime.state_db) as store:
        queue_video(store)
        pipeline = Pipeline(cfg, store, uploader=DryRunUploader())
        pipeline.prepare()
        assert pipeline.upload_pending() == 3
        assert all(Path(c.path).is_file() for c in store.clips_for_video("vid1"))

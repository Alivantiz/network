import time

from yt2tt.state import Store

VIDEO = {"video_id": "abc", "title": "Дорама", "url": "https://youtu.be/abc", "duration": 600}


def store(tmp_path):
    return Store(tmp_path / "state.db")


def test_video_is_added_once(tmp_path):
    with store(tmp_path) as s:
        assert s.add_video(VIDEO) is True
        assert s.add_video(VIDEO) is False
        assert s.has_video("abc") is True
        assert s.has_video("zzz") is False


def test_clip_lifecycle(tmp_path):
    with store(tmp_path) as s:
        s.add_video(VIDEO)
        s.add_clips(
            "abc",
            [
                {"idx": 1, "total": 2, "path": "/a.mp4", "start": 0, "duration": 60, "title": "1"},
                {"idx": 2, "total": 2, "path": "/b.mp4", "start": 60, "duration": 60, "title": "2"},
            ],
        )
        pending = s.pending_clips()
        assert [c.idx for c in pending] == [1, 2]
        assert s.video_is_complete("abc") is False

        s.set_clip_status(pending[0].id, "uploaded", publish_id="p1")
        assert s.video_is_complete("abc") is False
        s.set_clip_status(pending[1].id, "uploaded", publish_id="p2")
        assert s.video_is_complete("abc") is True
        assert s.pending_clips() == []


def test_clips_are_not_duplicated(tmp_path):
    with store(tmp_path) as s:
        s.add_video(VIDEO)
        clip = [{"idx": 1, "total": 1, "path": "/a.mp4", "start": 0, "duration": 60, "title": "1"}]
        s.add_clips("abc", clip)
        s.add_clips("abc", clip)
        assert len(s.clips_for_video("abc")) == 1


def test_upload_counters(tmp_path):
    with store(tmp_path) as s:
        s.add_video(VIDEO)
        s.add_clips(
            "abc",
            [{"idx": 1, "total": 1, "path": "/a.mp4", "start": 0, "duration": 60, "title": "t"}],
        )
        assert s.last_upload_ts() is None
        clip = s.pending_clips()[0]
        s.set_clip_status(clip.id, "uploaded")
        assert s.uploads_since(time.time() - 60) == 1
        assert s.uploads_since(time.time() + 60) == 0
        assert s.last_upload_ts() is not None


def test_status_transitions_and_summary(tmp_path):
    with store(tmp_path) as s:
        s.add_video(VIDEO)
        s.set_video_status("abc", "downloaded", source_path="/tmp/abc.mp4")
        s.set_video_status("abc", "split")
        row = s.get_video("abc")
        assert row["status"] == "split"
        assert row["source_path"] == "/tmp/abc.mp4"  # preserved across updates
        assert s.summary()["videos"] == {"split": 1}

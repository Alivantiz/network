import pytest

from yt2tt.config import SearchConfig
from yt2tt.errors import SearchError
from yt2tt.youtube import (
    VideoCandidate,
    YouTubeSearcher,
    dedupe,
    matches_filters,
    parse_iso8601_duration,
)


def candidate(**kw):
    base = {"video_id": "a", "title": "Дорама 1 серия", "url": "u", "duration": 1800}
    return VideoCandidate(**{**base, **kw})


@pytest.mark.parametrize(
    "value, expected",
    [("PT1H2M3S", 3723), ("PT45M", 2700), ("PT30S", 30), ("P1DT2H", 93600), ("PT0S", 0)],
)
def test_iso_durations(value, expected):
    assert parse_iso8601_duration(value) == expected


def test_bad_duration_raises():
    with pytest.raises(ValueError):
        parse_iso8601_duration("nonsense")


def test_duration_bounds():
    cfg = SearchConfig(min_duration_sec=240, max_duration_sec=3600)
    assert matches_filters(candidate(duration=1800), cfg)
    assert not matches_filters(candidate(duration=60), cfg)
    assert not matches_filters(candidate(duration=7200), cfg)


def test_unknown_duration_passes():
    assert matches_filters(candidate(duration=None), SearchConfig())


def test_exclude_and_require_keywords():
    cfg = SearchConfig(exclude_keywords=["трейлер"], require_keywords=["дорама"])
    assert matches_filters(candidate(), cfg)
    assert not matches_filters(candidate(title="Трейлер дорамы"), cfg)
    assert not matches_filters(candidate(title="Обычное видео"), cfg)


def test_excluded_channel():
    cfg = SearchConfig(exclude_channels=["UC123"])
    assert not matches_filters(candidate(channel_id="UC123"), cfg)


def test_dedupe_keeps_first_occurrence():
    videos = [candidate(title="first"), candidate(title="second"), candidate(video_id="b")]
    assert [v.title for v in dedupe(videos)] == ["first", "Дорама 1 серия"]


def test_backend_resolution():
    assert YouTubeSearcher(SearchConfig()).backend == "ytdlp"
    assert YouTubeSearcher(SearchConfig(youtube_api_key="k")).backend == "api"
    with pytest.raises(SearchError):
        YouTubeSearcher(SearchConfig(backend="api"))

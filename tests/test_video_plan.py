import pytest

from yt2tt.config import ClipConfig
from yt2tt.video import build_vfilter, plan_segments


def spans(segments):
    return [(round(s.start, 3), round(s.duration, 3)) for s in segments]


def test_exact_split():
    assert spans(plan_segments(180, part_seconds=60)) == [(0, 60), (60, 60), (120, 60)]


def test_short_tail_is_merged_into_previous_part():
    assert spans(plan_segments(190, part_seconds=60, min_part_seconds=20)) == [
        (0, 60),
        (60, 60),
        (120, 70),
    ]


def test_tail_is_kept_when_long_enough():
    assert spans(plan_segments(220, part_seconds=60, min_part_seconds=20))[-1] == (180, 40)


def test_single_short_video_still_yields_one_part():
    assert spans(plan_segments(12, part_seconds=60, min_part_seconds=20)) == [(0, 12)]


def test_intro_and_outro_are_trimmed():
    segments = plan_segments(300, part_seconds=60, skip_intro_seconds=30, skip_outro_seconds=30)
    assert spans(segments) == [
        (30, 60),
        (90, 60),
        (150, 60),
        (210, 60),
    ]


def test_max_parts_truncates():
    assert len(plan_segments(3600, part_seconds=60, max_parts=5)) == 5


def test_nothing_left_after_trim():
    assert plan_segments(50, part_seconds=60, skip_intro_seconds=30, skip_outro_seconds=30) == []


def test_indices_are_sequential_from_one():
    segments = plan_segments(300, part_seconds=60)
    assert [s.index for s in segments] == [1, 2, 3, 4, 5]


def test_invalid_part_length():
    with pytest.raises(ValueError):
        plan_segments(60, part_seconds=0)


@pytest.mark.parametrize("orientation", ["blur", "crop", "pad"])
def test_vfilter_targets_vertical_frame(orientation):
    chain = build_vfilter(ClipConfig(orientation=orientation))
    assert "1080:1920" in chain


def test_vfilter_none_means_no_filter():
    assert build_vfilter(ClipConfig(orientation="none")) is None

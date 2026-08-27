import pytest

from yt2tt.uploaders.tiktok import MAX_CHUNK, MIN_CHUNK, plan_chunks


def test_small_file_is_sent_whole():
    size = 3_000_000
    chunk_size, count, ranges = plan_chunks(size, 10 * 1024 * 1024)
    assert (chunk_size, count, ranges) == (size, 1, [(0, size - 1)])


def test_last_chunk_absorbs_the_remainder():
    size = 25 * 1024 * 1024
    chunk_size, count, ranges = plan_chunks(size, 10 * 1024 * 1024)
    assert count == 2
    assert ranges[-1] == (chunk_size, size - 1)
    assert sum(end - start + 1 for start, end in ranges) == size


def test_ranges_are_contiguous_and_cover_the_file():
    size = 137 * 1024 * 1024
    _, _, ranges = plan_chunks(size, 10 * 1024 * 1024)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == size - 1
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:], strict=False):
        assert next_start == prev_end + 1


def test_chunk_size_is_clamped_to_api_limits():
    chunk_size, _, _ = plan_chunks(500 * 1024 * 1024, 1024)
    assert chunk_size == MIN_CHUNK
    chunk_size, _, _ = plan_chunks(500 * 1024 * 1024, 999 * 1024 * 1024)
    assert chunk_size == MAX_CHUNK


def test_zero_size_is_rejected():
    with pytest.raises(ValueError):
        plan_chunks(0, MIN_CHUNK)

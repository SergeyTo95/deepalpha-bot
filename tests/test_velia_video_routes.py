import pytest

from velia_video_routes import parse_single_byte_range


def test_parses_open_closed_and_suffix_ranges():
    assert parse_single_byte_range("bytes=0-99", 1000) == (0, 99)
    assert parse_single_byte_range("bytes=900-", 1000) == (900, 999)
    assert parse_single_byte_range("bytes=-100", 1000) == (900, 999)


def test_clamps_end_to_available_bytes():
    assert parse_single_byte_range("bytes=900-5000", 1000) == (900, 999)


@pytest.mark.parametrize(
    "value",
    [
        "items=0-1",
        "bytes=",
        "bytes=1-0",
        "bytes=1000-",
        "bytes=0-1,3-4",
        "bytes=-0",
    ],
)
def test_rejects_invalid_or_multi_ranges(value):
    with pytest.raises(ValueError):
        parse_single_byte_range(value, 1000)

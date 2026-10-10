from datetime import datetime, timedelta, timezone

import pytest

from onedrive_fixer.naming import corrected_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("20220111_155512.jpg", "20220101_120000.jpg"),
        ("20220111_155512123.jpg", "20220101_120000_123.jpg"),
        ("20220111_155512123_iOS.MOV", "20220101_120000_123_iOS.MOV"),
        ("20220111_155512_123_iOS.heic", "20220101_120000_123_iOS.heic"),
        ("holiday_20220111_155512.jpg", None),
        ("20220231_155512.jpg", None),
        ("20220101_250000.jpg", None),
        ("holiday.jpg", None),
    ],
)
def test_filename_rules(name, expected):
    assert corrected_name(name, datetime(2022, 1, 1, 12)) == expected


def test_capture_offset_is_not_converted_to_host_timezone():
    date = datetime(2022, 1, 1, 12, tzinfo=timezone(timedelta(hours=9)))
    assert corrected_name("20220101_030000.jpg", date) == "20220101_120000.jpg"

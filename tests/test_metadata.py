from datetime import UTC, datetime, timedelta, timezone

import av
import pytest
from PIL import ExifTags, Image

from onedrive_fixer.adapters.metadata import MediaMetadataReader, parse_date
from onedrive_fixer.models import MediaMetadata, Status
from onedrive_fixer.services import scan_names


@pytest.mark.parametrize(
    "value",
    [
        "2022:01:01 12:00:00+02:00",
        "2022-01-01T12:00:00+02:00",
        "2022-01-01T12:00:00+0200",
        "2022:01:01 12:00:00+02:00\x00",
    ],
)
def test_exif_and_iso_dates(value):
    assert parse_date(value) == datetime(2022, 1, 1, 12, tzinfo=timezone(timedelta(hours=2)))


@pytest.mark.parametrize("value", ["", "0000:00:00 00:00:00", "not a timestamp"])
def test_unknown_metadata_date(value):
    assert parse_date(value) is None


@pytest.mark.parametrize(
    ("format", "extension"),
    [
        ("JPEG", ".JpG"),
        ("JPEG", ".jpeg"),
        ("PNG", ".png"),
        ("HEIF", ".HEIC"),
    ],
)
def test_real_photo_metadata_preserves_capture_wall_time(tmp_path, format, extension):
    reader = MediaMetadataReader()
    source = tmp_path / f"20220101_100000{extension}"
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Apple"
    exif[ExifTags.IFD.Exif] = {
        ExifTags.Base.DateTimeDigitized: "2022:01:01 12:00:00",
        ExifTags.Base.OffsetTimeDigitized: "+02:00",
        ExifTags.Base.DateTimeOriginal: "2021:12:31 10:00:00",
    }
    with Image.new("RGB", (16, 16), "red") as image:
        image.save(source, format=format, exif=exif)
    before = source.read_bytes()
    metadata = reader.read(source)
    assert metadata == MediaMetadata(
        "Apple", datetime(2022, 1, 1, 12, tzinfo=timezone(timedelta(hours=2)))
    )
    plans = scan_names(tmp_path, reader)
    assert plans[0].destination.name == f"20220101_120000{extension}"
    assert source.read_bytes() == before


def test_photo_without_metadata_is_skipped(tmp_path):
    source = tmp_path / "20220101_100000.jpg"
    with Image.new("RGB", (16, 16)) as image:
        image.save(source)
    assert MediaMetadataReader().read(source) == MediaMetadata()
    assert scan_names(tmp_path, MediaMetadataReader())[0].status == Status.NOT_APPLE


@pytest.mark.parametrize("raw_date", [None, "0000:00:00 00:00:00"])
def test_unknown_photo_date_has_no_filesystem_or_original_date_fallback(tmp_path, raw_date):
    source = tmp_path / "20220101_100000.jpg"
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Apple"
    details = {ExifTags.Base.DateTimeOriginal: "2022:01:01 12:00:00"}
    if raw_date is not None:
        details[ExifTags.Base.DateTimeDigitized] = raw_date
    exif[ExifTags.IFD.Exif] = details
    with Image.new("RGB", (16, 16)) as image:
        image.save(source, exif=exif)
    reader = MediaMetadataReader()
    assert reader.read(source) == MediaMetadata("Apple")
    assert scan_names(tmp_path, reader)[0].status == Status.UNKNOWN_DATE


def _make_video(source, tags):
    with av.open(str(source), mode="w", options={"movflags": "use_metadata_tags"}) as container:
        container.metadata.update(tags)
        stream = container.add_stream("mpeg4", rate=1)
        stream.width = stream.height = 16
        stream.pix_fmt = "yuv420p"
        frame = av.VideoFrame(16, 16, "rgb24")
        frame.planes[0].update(bytes(frame.planes[0].buffer_size))
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@pytest.mark.parametrize("extension", [".MOV", ".mp4"])
@pytest.mark.parametrize(
    ("capture", "expected"),
    [
        (
            "2024-11-11T20:43:49-0800",
            datetime(2024, 11, 11, 20, 43, 49, tzinfo=timezone(timedelta(hours=-8))),
        ),
        (None, datetime(2024, 11, 12, 4, 43, 49, tzinfo=UTC)),
        ("invalid", datetime(2024, 11, 12, 4, 43, 49, tzinfo=UTC)),
        ("2024-11-11T20:43:49", datetime(2024, 11, 12, 4, 43, 49, tzinfo=UTC)),
    ],
)
def test_real_video_metadata_and_capture_offset(
    tmp_path, monkeypatch, extension, capture, expected
):
    source = tmp_path / f"20241112_044349000_iOS{extension}"
    tags = {"com.apple.quicktime.make": "Apple", "creation_time": "2024-11-12T04:43:49Z"}
    if capture is not None:
        tags["com.apple.quicktime.creationdate"] = capture
    _make_video(source, tags)
    before = source.read_bytes()

    def unexpected_process(*args, **kwargs):
        pytest.fail("Video metadata reading attempted to launch an external executable")

    monkeypatch.setattr("subprocess.Popen", unexpected_process)
    reader = MediaMetadataReader()
    metadata = reader.read(source)
    assert metadata == MediaMetadata("Apple", expected)
    assert metadata.created_at.isoformat() == expected.isoformat()
    assert source.read_bytes() == before


def test_video_without_creation_time_is_unknown_even_with_capture_date(tmp_path):
    source = tmp_path / "20241112_044349.MOV"
    _make_video(
        source, {"make": "Apple", "com.apple.quicktime.creationdate": "2024-11-11T20:43:49-0800"}
    )
    reader = MediaMetadataReader()
    assert reader.read(source) == MediaMetadata("Apple")
    assert scan_names(tmp_path, reader)[0].status == Status.UNKNOWN_DATE


def test_conflicting_video_creation_dates_are_unknown(tmp_path):
    source = tmp_path / "20241112_044349.MOV"
    _make_video(
        source,
        {
            "com.apple.quicktime.make": "Apple",
            "creation_time": "2024-11-12T04:43:49Z;2025-11-12T04:43:49Z",
        },
    )
    reader = MediaMetadataReader()
    assert reader.read(source) == MediaMetadata("Apple")
    assert scan_names(tmp_path, reader)[0].status == Status.UNKNOWN_DATE


@pytest.mark.parametrize("extension", [".jpg", ".heic", ".png", ".mov", ".mp4"])
def test_corrupt_media_is_reported_without_stopping_scan(tmp_path, extension):
    (tmp_path / f"20220101_100000{extension}").write_bytes(b"not media")
    plans = scan_names(tmp_path, MediaMetadataReader())
    assert len(plans) == 1 and plans[0].status == Status.ERROR
    assert plans[0].message

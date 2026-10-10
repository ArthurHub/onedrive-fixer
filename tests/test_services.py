from datetime import datetime

import pytest
from PIL import Image
from pillow_heif import register_heif_opener

from onedrive_fixer.adapters.images import PillowHeicConverter
from onedrive_fixer.models import MediaMetadata, Status
from onedrive_fixer.services import convert_heic, fix_names, scan_heic, scan_names


class Metadata:
    def read(self, source):
        if source.name.startswith("broken"):
            raise ValueError("bad metadata")
        if source.name.startswith("samsung"):
            return MediaMetadata("Samsung", datetime(2022, 1, 1, 12))
        if source.name.startswith("missing"):
            return MediaMetadata("Apple")
        return MediaMetadata("aPpLe", datetime(2022, 1, 1, 12))


class RecordingTrash:
    def __init__(self):
        self.paths = []

    def move(self, source):
        assert source.with_suffix(".jpeg").is_file()
        with Image.open(source.with_suffix(".jpeg")) as output:
            output.load()
        self.paths.append(source)


@pytest.fixture
def heic(tmp_path):
    register_heif_opener()
    source = tmp_path / "photo.HEIC"
    exif = Image.Exif()
    exif[271] = "Apple"
    exif[306] = "2022:01:01 12:00:00"
    with Image.new("RGB", (32, 24), "red") as image:
        image.save(source, format="HEIF", exif=exif)
    return source


def test_recursive_scan_and_statuses(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    for name in [
        "20220111_155512123_iOS.MOV",
        "20220101_120000.jpg",
        "samsung.JPG",
        "missing.heic",
        "broken.mp4",
        "holiday.png",
        "ignore.txt",
    ]:
        (child / name).write_bytes(b"original")
    events = []
    plans = scan_names(tmp_path, Metadata(), events.append)
    statuses = {plan.source.name: plan.status for plan in plans}
    assert statuses == {
        "20220111_155512123_iOS.MOV": Status.READY,
        "20220101_120000.jpg": Status.UNCHANGED,
        "samsung.JPG": Status.NOT_APPLE,
        "missing.heic": Status.UNKNOWN_DATE,
        "broken.mp4": Status.ERROR,
        "holiday.png": Status.UNKNOWN_DATE,
    }
    assert len(events) == 6
    assert events[-1].current == events[-1].total == 6


def test_preview_then_apply_does_not_mutate_plan(tmp_path):
    source = tmp_path / "20220111_155512123.jpg"
    source.write_bytes(b"original")
    plans = scan_names(tmp_path, Metadata())
    preview = fix_names(plans)
    assert preview[0].dry_run and preview[0].status == Status.READY
    assert source.read_bytes() == b"original"
    assert sorted(path.name for path in tmp_path.iterdir()) == [source.name]
    result = fix_names(plans, dry_run=False)[0]
    assert result.status == Status.COMPLETE
    assert plans[0].status == Status.READY
    assert not source.exists()
    assert (tmp_path / "20220101_120000_123.jpg").read_bytes() == b"original"


def test_existing_destination_and_duplicate_plans(tmp_path):
    for name in [
        "20220111_155512.jpg",
        "20220111_155512.JPG",
        "20220111_155512001.jpg",
        "20220101_120000_001.jpg",
    ]:
        (tmp_path / name).write_bytes(b"keep")
    # Two different seconds can independently map to the same capture time.
    (tmp_path / "20220112_155512.jpg").write_bytes(b"keep")
    plans = scan_names(tmp_path, Metadata())
    ready = [plan for plan in plans if plan.status == Status.READY]
    assert ready == []
    conflicts = [plan for plan in plans if plan.status == Status.CONFLICT]
    assert len(conflicts) >= 3
    results = fix_names(plans, dry_run=False)
    assert all(result.status != Status.COMPLETE for result in results)
    assert (tmp_path / "20220101_120000_001.jpg").read_bytes() == b"keep"


def test_destination_created_after_preview_is_never_overwritten(tmp_path):
    source = tmp_path / "20220111_155512.jpg"
    source.write_bytes(b"source")
    plans = scan_names(tmp_path, Metadata())
    target = tmp_path / "20220101_120000.jpg"
    target.write_bytes(b"existing")
    assert fix_names(plans, dry_run=False)[0].status == Status.CONFLICT
    assert source.read_bytes() == b"source"
    assert target.read_bytes() == b"existing"


def test_atomic_rename_refuses_a_destination_created_during_publication(tmp_path, monkeypatch):
    from onedrive_fixer import services

    source = tmp_path / "20220111_155512.jpg"
    source.write_bytes(b"original")
    plans = scan_names(tmp_path, Metadata())
    original_publish = services._publish

    def competing_publish(source_path, destination):
        destination.write_bytes(b"another process")
        original_publish(source_path, destination)

    monkeypatch.setattr(services, "_publish", competing_publish)
    result = fix_names(plans, dry_run=False)[0]
    assert result.status == Status.CONFLICT
    assert source.read_bytes() == b"original"
    assert plans[0].destination.read_bytes() == b"another process"


def test_changed_source_and_per_file_error_continuation(tmp_path):
    first = tmp_path / "20220111_155512.jpg"
    second = tmp_path / "20220111_155512.heic"
    first.write_bytes(b"original")
    second.write_bytes(b"original")
    plans = scan_names(tmp_path, Metadata())
    first.write_bytes(b"changed content")
    results = fix_names(plans, dry_run=False)
    assert {result.status for result in results} == {Status.ERROR, Status.COMPLETE}
    assert first.read_bytes() == b"changed content"


def test_heic_dry_run_performs_no_codec_or_trash_operations(heic):
    class Unused:
        def convert(self, *args):
            pytest.fail("Dry run attempted conversion")

        def move(self, *args):
            pytest.fail("Dry run attempted recycling")

    before = heic.read_bytes()
    results = convert_heic(scan_heic(heic.parent), Unused(), Unused())
    assert results[0].status == Status.READY and results[0].dry_run
    assert heic.read_bytes() == before
    assert list(heic.parent.iterdir()) == [heic]


@pytest.mark.parametrize("keep_original", [True, False])
def test_real_heic_conversion_and_metadata(heic, keep_original):
    trash = RecordingTrash()
    results = convert_heic(
        scan_heic(heic.parent),
        PillowHeicConverter(),
        trash,
        dry_run=False,
        keep_original=keep_original,
    )
    assert results[0].status == Status.COMPLETE
    assert trash.paths == ([] if keep_original else [heic])
    with Image.open(heic.with_suffix(".jpeg")) as jpeg:
        assert jpeg.format == "JPEG"
        assert jpeg.size == (32, 24)
        assert jpeg.getexif()[271] == "Apple"
        assert jpeg.getpixel((0, 0))[0] > 240


def test_multi_image_heic_exports_primary_image(tmp_path):
    register_heif_opener()
    source = tmp_path / "multiple.heic"
    destination = tmp_path / "primary.jpeg"
    with (
        Image.new("RGB", (16, 16), "red") as first,
        Image.new("RGB", (32, 24), "blue") as primary,
    ):
        first.save(source, format="HEIF", save_all=True, append_images=[primary], primary_index=1)
    PillowHeicConverter().convert(source, destination, 100)
    with Image.open(destination) as output:
        assert output.size == (32, 24)
        assert output.getpixel((0, 0))[2] > 240


def test_corrupt_heic_preserves_source_and_cleans_staging(tmp_path):
    source = tmp_path / "corrupt.heic"
    source.write_bytes(b"not HEIC")
    trash = RecordingTrash()
    result = convert_heic(scan_heic(tmp_path), PillowHeicConverter(), trash, dry_run=False)[0]
    assert result.status == Status.ERROR
    assert trash.paths == []
    assert source.read_bytes() == b"not HEIC"
    assert list(tmp_path.iterdir()) == [source]


def test_recycle_failure_keeps_jpeg_and_source(heic):
    class FailingTrash:
        def move(self, source):
            raise OSError("Recycle Bin unavailable")

    result = convert_heic(
        scan_heic(heic.parent), PillowHeicConverter(), FailingTrash(), dry_run=False
    )[0]
    assert result.status == Status.ERROR
    assert "JPEG saved" in result.message
    assert heic.exists() and heic.with_suffix(".jpeg").exists()
    assert scan_heic(heic.parent)[0].status == Status.CONFLICT


def test_conversion_publication_race_preserves_destination_and_original(heic):
    class RacingConverter:
        def convert(self, source, destination, quality):
            PillowHeicConverter().convert(source, destination, quality)
            source.with_suffix(".jpeg").write_bytes(b"another process")

    trash = RecordingTrash()
    result = convert_heic(scan_heic(heic.parent), RacingConverter(), trash, dry_run=False)[0]
    assert result.status == Status.CONFLICT
    assert heic.exists()
    assert heic.with_suffix(".jpeg").read_bytes() == b"another process"
    assert trash.paths == []
    assert not list(heic.parent.glob(".onedrive-fixer-*"))


def test_scanning_a_file_fails(tmp_path):
    source = tmp_path / "photo.heic"
    source.touch()
    with pytest.raises(NotADirectoryError):
        scan_heic(source)


def test_symlinks_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.heic").touch()
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
        (root / "linked.heic").symlink_to(outside / "hidden.heic")
    except OSError:
        pytest.skip("Creating symlinks requires Windows Developer Mode or elevated privileges")
    assert scan_heic(root) == []

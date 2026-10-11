import random
from io import BytesIO

import pytest
from PIL import Image, ImageCms, JpegImagePlugin

from onedrive_fixer.adapters.images import PillowJpegOptimizer
from onedrive_fixer.models import Status
from onedrive_fixer.services import optimize_jpeg, scan_jpeg


@pytest.fixture
def jpeg(tmp_path):
    source = tmp_path / "20260820_075317_294_iOS.jpeg"
    exif = Image.Exif()
    exif[271] = "Apple"
    exif[306] = "2026:08:20 07:53:17"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    pixels = random.Random(42).randbytes(128 * 96 * 3)
    with Image.frombytes("RGB", (128, 96), pixels) as image:
        image.save(
            source,
            format="JPEG",
            quality=100,
            subsampling=0,
            exif=exif,
            icc_profile=icc,
            xmp=b"<x:xmpmeta />",
        )
    return source


class RecordingTrash:
    def __init__(self):
        self.originals = {}

    def move(self, source):
        # Model successful recycling without touching the real Recycle Bin.
        self.originals[source] = source.read_bytes()
        source.unlink()


@pytest.mark.parametrize(
    "quality, sampling, expected", [(100, 0, True), (100, 2, True), (95, 0, False), (90, 2, False)]
)
def test_quality_detection_reads_headers_without_decoding(
    tmp_path, monkeypatch, quality, sampling, expected
):
    source = tmp_path / "photo_iOS.jpeg"
    with Image.new("RGB", (16, 16), "red") as image:
        image.save(source, quality=quality, subsampling=sampling)

    def no_decode(*args, **kwargs):
        pytest.fail("Scanning decoded JPEG pixels")

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "load", no_decode)
    assert PillowJpegOptimizer().is_quality_100(source) is expected


def test_scan_filters_names_before_opening_and_recurses(jpeg, monkeypatch):
    root = jpeg.parent
    for name in ["holiday.jpeg", "photo_iOS.jpg", "photo_iOS.heic", "photo_iOS.jpeg.bak"]:
        (root / name).write_bytes(b"not an image; must never be opened")
    child = root / "child"
    child.mkdir()
    other = child / "PHOTO_IOS.JPEG"
    with Image.new("RGB", (16, 16), "blue") as image:
        image.save(other, quality=100, subsampling=2)
    opened = []
    image_open = Image.open

    def record_open(path, *args, **kwargs):
        opened.append(path)
        return image_open(path, *args, **kwargs)

    monkeypatch.setattr(Image, "open", record_open)
    events = []
    plans = scan_jpeg(root, PillowJpegOptimizer(), events.append)
    assert opened == [jpeg, other]
    assert [plan.source for plan in plans] == [jpeg, other]
    assert all(plan.status == Status.READY for plan in plans)
    assert events[-1].current == events[-1].total == 2


def test_scan_skips_lower_quality_and_reports_corrupt_candidates(tmp_path):
    good = tmp_path / "good_iOS.jpeg"
    with Image.new("RGB", (16, 16)) as image:
        image.save(good, quality=90, subsampling=0)
    (tmp_path / "broken_iOS.jpeg").write_bytes(b"broken JPEG")
    with Image.new("RGB", (16, 16)) as image:
        image.save(tmp_path / "disguised_iOS.jpeg", format="PNG")
    plans = scan_jpeg(tmp_path, PillowJpegOptimizer())
    assert {plan.source.name: plan.status for plan in plans} == {
        "good_iOS.jpeg": Status.UNCHANGED,
        "broken_iOS.jpeg": Status.ERROR,
        "disguised_iOS.jpeg": Status.ERROR,
    }


def test_preview_writes_nothing_and_keeps_diagnostic_message(jpeg):
    original = jpeg.read_bytes()
    plans = scan_jpeg(jpeg.parent, PillowJpegOptimizer())

    class Unused:
        def convert(self, *args):
            pytest.fail("Preview attempted encoding")

        def is_quality_100(self, *args):
            pytest.fail("Preview reopened an inspected image")

        def move(self, *args):
            pytest.fail("Preview attempted recycling")

    result = optimize_jpeg(plans, Unused(), Unused())[0]
    assert result.status == Status.READY and result.dry_run
    assert result.bytes_saved == 0
    assert "Quality-100" in result.message
    assert jpeg.read_bytes() == original
    assert list(jpeg.parent.iterdir()) == [jpeg]


@pytest.mark.parametrize("keep_original", [False, True])
def test_apply_reduces_size_preserves_metadata_and_is_idempotent(jpeg, keep_original):
    original = jpeg.read_bytes()
    optimizer = PillowJpegOptimizer()
    trash = RecordingTrash()
    result = optimize_jpeg(
        scan_jpeg(jpeg.parent, optimizer),
        optimizer,
        trash,
        dry_run=False,
        keep_original=keep_original,
    )[0]
    assert result.status == Status.COMPLETE
    assert "saved" in result.message
    assert jpeg.stat().st_size < len(original)
    assert result.bytes_saved == len(original) - jpeg.stat().st_size
    backup = jpeg.with_name(jpeg.name + ".bak")
    if keep_original:
        assert backup.read_bytes() == original
        assert not trash.originals
    else:
        assert not backup.exists()
        assert trash.originals == {backup: original}
    with Image.open(jpeg) as output:
        assert output.size == (128, 96)
        assert JpegImagePlugin.get_sampling(output) == 2
        assert output.quantization[0][0] == 3
        assert output.getexif()[271] == "Apple"
        with Image.open(BytesIO(original)) as before:
            assert output.info["icc_profile"] == before.info["icc_profile"]
            assert output.info["xmp"] == before.info["xmp"]
    optimized = jpeg.read_bytes()
    again = optimize_jpeg(scan_jpeg(jpeg.parent, optimizer), optimizer, trash, dry_run=False)[0]
    assert again.status == Status.UNCHANGED
    assert again.bytes_saved == 0
    assert jpeg.read_bytes() == optimized
    assert not list(jpeg.parent.glob(".onedrive-fixer-*"))


def test_existing_backup_is_a_conflict(jpeg):
    original = jpeg.read_bytes()
    backup = jpeg.with_name(jpeg.name + ".bak")
    backup.write_bytes(b"keep this backup")
    optimizer = PillowJpegOptimizer()
    result = optimize_jpeg(
        scan_jpeg(jpeg.parent, optimizer), optimizer, RecordingTrash(), dry_run=False
    )[0]
    assert result.status == Status.CONFLICT
    assert backup.read_bytes() == b"keep this backup"
    assert jpeg.read_bytes() == original


def test_changed_source_is_rejected(jpeg):
    optimizer = PillowJpegOptimizer()
    plans = scan_jpeg(jpeg.parent, optimizer)
    jpeg.write_bytes(b"changed since scan")
    result = optimize_jpeg(plans, optimizer, RecordingTrash(), dry_run=False)[0]
    assert result.status == Status.ERROR
    assert "changed since" in result.message
    assert jpeg.read_bytes() == b"changed since scan"


@pytest.mark.parametrize("failure", ["codec", "larger", "empty", "backup_race", "source_race"])
def test_encoding_failures_and_races_preserve_source(jpeg, failure):
    original = jpeg.read_bytes()
    backup = jpeg.with_name(jpeg.name + ".bak")

    class Optimizer(PillowJpegOptimizer):
        def convert(self, source, destination, quality):
            if failure == "codec":
                destination.write_bytes(b"incomplete")
                raise ValueError("codec failed")
            if failure == "larger":
                destination.write_bytes(original)
            elif failure == "empty":
                destination.write_bytes(b"")
            else:
                super().convert(source, destination, quality)
                if failure == "backup_race":
                    backup.write_bytes(b"another process")
                elif failure == "source_race":
                    source.write_bytes(b"another process")

    optimizer = Optimizer()
    trash = RecordingTrash()
    result = optimize_jpeg(scan_jpeg(jpeg.parent, optimizer), optimizer, trash, dry_run=False)[0]
    expected = (
        Status.UNCHANGED
        if failure == "larger"
        else (Status.CONFLICT if failure == "backup_race" else Status.ERROR)
    )
    assert result.status == expected
    assert result.bytes_saved == 0
    assert jpeg.read_bytes() == (b"another process" if failure == "source_race" else original)
    if failure == "backup_race":
        assert backup.read_bytes() == b"another process"
    else:
        assert not backup.exists()
    assert not trash.originals
    assert not list(jpeg.parent.glob(".onedrive-fixer-*"))


def test_publication_failure_preserves_original_and_backup(jpeg, monkeypatch):
    from pathlib import Path

    original = jpeg.read_bytes()
    optimizer = PillowJpegOptimizer()

    def fail_replace(*args):
        raise PermissionError("replacement blocked")

    monkeypatch.setattr(Path, "replace", fail_replace)
    result = optimize_jpeg(
        scan_jpeg(jpeg.parent, optimizer), optimizer, RecordingTrash(), dry_run=False
    )[0]
    assert result.status == Status.ERROR
    assert jpeg.read_bytes() == original
    assert jpeg.with_name(jpeg.name + ".bak").read_bytes() == original
    assert not list(jpeg.parent.glob(".onedrive-fixer-*"))


def test_backup_copy_failure_cleans_partial_backup(jpeg, monkeypatch):
    original = jpeg.read_bytes()

    def fail_copy(source, destination):
        destination.write(b"partial backup")
        raise OSError("disk full")

    monkeypatch.setattr("onedrive_fixer.services.shutil.copyfileobj", fail_copy)
    optimizer = PillowJpegOptimizer()
    result = optimize_jpeg(
        scan_jpeg(jpeg.parent, optimizer), optimizer, RecordingTrash(), dry_run=False
    )[0]
    assert result.status == Status.ERROR
    assert jpeg.read_bytes() == original
    assert not jpeg.with_name(jpeg.name + ".bak").exists()
    assert not list(jpeg.parent.glob(".onedrive-fixer-*"))


def test_source_changed_during_backup_is_not_replaced(jpeg, monkeypatch):
    import shutil

    original = jpeg.read_bytes()
    copy = shutil.copyfileobj

    def change_source_after_copy(source, destination):
        copy(source, destination)
        jpeg.write_bytes(b"changed while copying")

    monkeypatch.setattr("onedrive_fixer.services.shutil.copyfileobj", change_source_after_copy)
    optimizer = PillowJpegOptimizer()
    result = optimize_jpeg(
        scan_jpeg(jpeg.parent, optimizer), optimizer, RecordingTrash(), dry_run=False
    )[0]
    assert result.status == Status.ERROR
    assert "backup kept" in result.message
    assert jpeg.read_bytes() == b"changed while copying"
    assert jpeg.with_name(jpeg.name + ".bak").read_bytes() == original
    assert not list(jpeg.parent.glob(".onedrive-fixer-*"))


def test_truncated_pixel_data_is_rejected_during_apply(jpeg):
    original = jpeg.read_bytes()
    scan_start = original.index(b"\xff\xda")
    header_size = int.from_bytes(original[scan_start + 2 : scan_start + 4], "big")
    truncated = original[: scan_start + 2 + header_size + 1]
    jpeg.write_bytes(truncated)
    optimizer = PillowJpegOptimizer()
    plans = scan_jpeg(jpeg.parent, optimizer)
    assert plans[0].status == Status.READY  # Header inspection does not decode pixels.
    result = optimize_jpeg(plans, optimizer, RecordingTrash(), dry_run=False)[0]
    assert result.status == Status.ERROR
    assert jpeg.read_bytes() == truncated
    assert not jpeg.with_name(jpeg.name + ".bak").exists()
    assert not list(jpeg.parent.glob(".onedrive-fixer-*"))


def test_recycle_failure_keeps_optimized_jpeg_and_original_backup(jpeg):
    original = jpeg.read_bytes()

    class FailingTrash:
        def move(self, source):
            raise OSError("recycling failed")

    optimizer = PillowJpegOptimizer()
    result = optimize_jpeg(
        scan_jpeg(jpeg.parent, optimizer), optimizer, FailingTrash(), dry_run=False
    )[0]
    assert result.status == Status.ERROR
    assert "JPEG optimized" in result.message
    assert jpeg.stat().st_size < len(original)
    assert result.bytes_saved == len(original) - jpeg.stat().st_size
    assert jpeg.with_name(jpeg.name + ".bak").read_bytes() == original


@pytest.mark.parametrize("quality", [0, 101])
def test_invalid_quality_is_rejected(quality):
    with pytest.raises(ValueError, match="quality"):
        optimize_jpeg([], PillowJpegOptimizer(), RecordingTrash(), quality=quality)

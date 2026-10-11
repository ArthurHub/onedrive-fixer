import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def executable(tmp_path_factory):
    configured = os.environ.get("ONEDRIVE_FIXER_EXE")
    if not configured:
        pytest.skip("Set ONEDRIVE_FIXER_EXE to test a built executable")
    source = Path(configured).resolve(strict=True)
    folder = tmp_path_factory.mktemp("standalone executable")
    return Path(shutil.copy2(source, folder / source.name))


@pytest.fixture
def run_executable(executable, tmp_path):
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(name, None)
    environment["PATH"] = str(Path(environment["SYSTEMROOT"]) / "System32")

    def run(*arguments, expected_code=0):
        result = subprocess.run(
            [str(executable), *map(str, arguments)],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == expected_code, result.stdout + result.stderr
        return result

    return run


def test_executable_help_and_invalid_arguments(run_executable):
    result = run_executable("--help")
    assert all(name in result.stdout for name in ("fix-names", "convert-heic", "optimize-jpeg"))
    assert result.stderr == ""
    assert "invalid choice" in run_executable("unknown", expected_code=2).stderr


def test_executable_reads_photo_and_video_metadata(run_executable, tmp_path):
    import av
    from PIL import ExifTags, Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Apple"
    exif[ExifTags.IFD.Exif] = {ExifTags.Base.DateTimeDigitized: "2022:01:01 12:00:00"}
    for format_name, extension in (("JPEG", ".jpg"), ("PNG", ".png"), ("HEIF", ".heic")):
        with Image.new("RGB", (16, 16), "red") as image:
            image.save(tmp_path / f"20220101_100000{extension}", format=format_name, exif=exif)
    for extension in (".mov", ".mp4"):
        with av.open(
            str(tmp_path / f"20220101_100000{extension}"),
            mode="w",
            options={"movflags": "use_metadata_tags"},
        ) as container:
            container.metadata.update({"make": "Apple", "creation_time": "2022-01-01T12:00:00Z"})
            stream = container.add_stream("mpeg4", rate=1)
            stream.width = stream.height = 16
            stream.pix_fmt = "yuv420p"
            frame = av.VideoFrame(16, 16, "rgb24")
            frame.planes[0].update(bytes(frame.planes[0].buffer_size))
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
    originals = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    preview = json.loads(run_executable("fix-names", tmp_path, "--json").stdout)
    assert len(preview) == 5
    assert all(item["status"] == "update_required" and item["dry_run"] for item in preview)
    assert originals == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    applied = json.loads(run_executable("fix-names", tmp_path, "--apply", "--yes", "--json").stdout)
    assert len(applied) == 5
    assert all(item["status"] == "update_complete" for item in applied)
    for name, content in originals.items():
        assert (tmp_path / name.replace("100000", "120000")).read_bytes() == content


def test_executable_converts_heic_with_metadata(run_executable, tmp_path):
    from PIL import ExifTags, Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    source = tmp_path / "photo.heic"
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Apple"
    with Image.new("RGB", (16, 16), "blue") as image:
        image.save(source, format="HEIF", exif=exif)
    original = source.read_bytes()
    result = run_executable(
        "convert-heic", tmp_path, "--apply", "--yes", "--keep-original", "--json"
    )
    assert json.loads(result.stdout)[0]["status"] == "update_complete"
    assert source.read_bytes() == original
    with Image.open(source.with_suffix(".jpeg")) as image:
        image.load()
        assert image.format == "JPEG" and image.size == (16, 16)
        assert image.getexif()[ExifTags.Base.Make] == "Apple"


def test_executable_optimizes_jpeg_with_backup(run_executable, tmp_path):
    from PIL import Image

    source = tmp_path / "photo_iOS.jpeg"
    with Image.effect_noise((128, 128), 80).convert("RGB") as image:
        image.save(source, quality=100)
    original = source.read_bytes()
    result = run_executable(
        "optimize-jpeg", tmp_path, "--apply", "--yes", "--keep-original", "--json"
    )
    output = json.loads(result.stdout)[0]
    assert output["status"] == "update_complete"
    assert output["bytes_saved"] == len(original) - source.stat().st_size > 0
    assert source.with_suffix(".jpeg.bak").read_bytes() == original
    with Image.open(source) as image:
        image.load()
        assert image.size == (128, 128)

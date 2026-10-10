"""Optional parity checks against read-only original media; never bundle personal fixtures."""

import os
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from onedrive_fixer.adapters.images import PillowHeicConverter
from onedrive_fixer.adapters.metadata import MediaMetadataReader
from onedrive_fixer.models import FileStamp, Status
from onedrive_fixer.services import scan_names

pytestmark = pytest.mark.legacy


@pytest.fixture
def media_root():
    value = os.environ.get("ONEDRIVE_FIXER_LEGACY_MEDIA")
    if not value:
        pytest.skip("Set ONEDRIVE_FIXER_LEGACY_MEDIA to check the original sample media")
    return Path(value)


def test_original_scan_results(media_root):
    reader = MediaMetadataReader()
    plans = scan_names(media_root, reader)
    assert {plan.destination.name for plan in plans if plan.status == Status.READY} == {
        "20220412_132854_446_iOS.jpg",
        "20241101_094622_039_iOS.heic",
        "20241111_204349_000_iOS.MOV",
    }
    assert [plan.source.name for plan in plans if plan.status == Status.UNCHANGED] == [
        "20231202_104509_739_iOS.heic"
    ]
    assert [plan.source.name for plan in plans if plan.status == Status.NOT_APPLE] == [
        "20231210_095729.jPG"
    ]
    assert len(plans) == 5


def test_original_heic_decoding(media_root, tmp_path):
    sources = list(media_root.rglob("*.heic"))
    assert len(sources) == 2
    converter = PillowHeicConverter()
    for source in sources:
        before = FileStamp.read(source)
        digest = sha256(source.read_bytes()).digest()
        destination = tmp_path / source.with_suffix(".jpeg").name
        converter.convert(source, destination, 100)
        with Image.open(destination) as output:
            output.load()
            assert output.format == "JPEG"
            assert output.getexif()[271] == "Apple"
        assert FileStamp.read(source) == before
        assert sha256(source.read_bytes()).digest() == digest

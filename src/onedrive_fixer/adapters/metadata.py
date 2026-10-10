"""
Read photo and video metadata through installed Python libraries.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from PIL import ExifTags, Image
from pillow_heif import register_heif_opener

from onedrive_fixer.models import MediaMetadata


def _text(value: object) -> str:
    """
    Normalize text metadata, tolerating byte values and trailing EXIF padding.
    Treat non-text values as missing rather than inventing metadata from them.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value.strip("\x00 \t\r\n") if isinstance(value, str) else ""


def parse_date(value: str) -> datetime | None:
    """
    Parse EXIF or ISO timestamps while preserving any explicit timezone offset.
    Return None for invalid values without substituting the computer's timezone.
    """
    normalized = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", _text(value))
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _video_date(value: object) -> datetime | None:
    """
    Accept matching duplicate timestamps joined by FFmpeg with semicolons.
        Return None for invalid or conflicting values rather than guessing a capture time.
    """
    dates = [parse_date(part) for part in _text(value).split(";")]
    first = dates[0]
    if first is None:
        return None
    if any(date is None or date.isoformat() != first.isoformat() for date in dates[1:]):
        return None
    return first


class MediaMetadataReader:
    """
    Metadata adapter using Pillow for photos and PyAV for MOV/MP4 containers.
    Preserve photo wall time and use explicit Apple capture offsets for video dates.
    """

    def __init__(self) -> None:
        """
        Register HEIF metadata support with Pillow without locating external executables.
        """
        register_heif_opener()

    def read(self, source: Path) -> MediaMetadata:
        """
        Read camera make and creation time without changing the source or decoding video.
        Missing tags return empty values; filesystem and metadata-reading errors propagate.
        """
        if source.suffix.lower() in {".mov", ".mp4"}:
            return self._read_video(source)
        return self._read_photo(source)

    def _read_photo(self, source: Path) -> MediaMetadata:
        """
        Read EXIF Make and DateTimeDigitized, equivalent to the former CreateDate field.
        Retain the digitized offset when valid, without converting the photo's wall time.
        """
        with Image.open(source) as image:
            exif = image.getexif()
            details = exif.get_ifd(ExifTags.IFD.Exif)
            make = _text(exif.get(ExifTags.Base.Make))
            raw_date = _text(details.get(ExifTags.Base.DateTimeDigitized))
            offset = _text(details.get(ExifTags.Base.OffsetTimeDigitized))
            created_at = parse_date(raw_date + offset) or parse_date(raw_date)
        return MediaMetadata(make, created_at)

    def _read_video(self, source: Path) -> MediaMetadata:
        """
        Read container creation time and camera make through PyAV's bundled FFmpeg libraries.
        Convert UTC creation time to an explicit Apple capture offset when available.
        """
        import av

        with av.open(str(source), mode="r", timeout=60.0) as container:
            tags = {key.casefold(): value for key, value in container.metadata.items()}
            make = _text(tags.get("com.apple.quicktime.make")) or _text(tags.get("make"))
            created_at = _video_date(tags.get("creation_time"))
            capture_date = _video_date(tags.get("com.apple.quicktime.creationdate"))
        if created_at is not None and capture_date is not None and capture_date.tzinfo is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            created_at = created_at.astimezone(capture_date.tzinfo)
        return MediaMetadata(make, created_at)

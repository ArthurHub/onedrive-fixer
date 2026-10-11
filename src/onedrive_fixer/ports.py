"""
Small adapter contracts; the services never import a UI or imaging library.
"""

from pathlib import Path
from typing import Protocol

from onedrive_fixer.models import MediaMetadata


class MetadataReader(Protocol):
    """
    Metadata backend used by the filename scanning service.
    """

    def read(self, source: Path) -> MediaMetadata:
        """
        Read camera make and capture time without changing the source file.
        Return missing values for absent tags; raise when metadata cannot be read.
        """
        ...


class ImageConverter(Protocol):
    """
    Image backend that writes a verified JPEG to a service-owned staging path.
    """

    def convert(self, source: Path, destination: Path, quality: int) -> None:
        """
        Encode the HEIC primary image as JPEG with quality from 1 to 100.
        Verify the output before returning; never remove or change the source.
        """
        ...


class Trash(Protocol):
    """
    Recoverable file-removal backend used after successful conversion.
    """

    def move(self, source: Path) -> None:
        """
        Move the source to the operating system's trash or Recycle Bin.
        Raise on failure instead of falling back to permanent deletion.
        """
        ...


class JpegOptimizer(Protocol):
    """
    JPEG header inspection and verified re-encoding without modifying the source.
    """

    def is_quality_100(self, source: Path) -> bool:
        """
        Check JPEG quantization tables for quality 100 without decoding pixels.
        Raise for invalid or unsupported files.
        """
        ...

    def convert(self, source: Path, destination: Path, quality: int) -> None:
        """
        Write and verify an optimized JPEG while retaining supported photo metadata.
        Leave the source intact on success or failure.
        """
        ...

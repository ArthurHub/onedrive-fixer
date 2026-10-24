"""
HEIC decoding and verified JPEG encoding using Pillow and libheif.
"""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, JpegImagePlugin
from pillow_heif import register_heif_opener
from send2trash import send2trash


def _save_jpeg(image: Image.Image, destination: Path, quality: int) -> None:
    """
    Normalize orientation and write an optimized 4:2:0 JPEG with photo metadata.
    Decode the saved output to verify its format and dimensions.
    """
    oriented = ImageOps.exif_transpose(image)
    try:
        with oriented.convert("RGB") as rgb:
            options = {}
            if exif := oriented.getexif():
                options["exif"] = exif.tobytes()
            if icc := oriented.info.get("icc_profile"):
                options["icc_profile"] = icc
            if xmp := oriented.info.get("xmp"):
                options["xmp"] = xmp
            rgb.save(
                destination,
                format="JPEG",
                quality=quality,
                subsampling=2,
                optimize=True,
                **options,
            )
            expected_size = rgb.size
    finally:
        oriented.close()
    with Image.open(destination) as output:
        if output.format != "JPEG" or output.size != expected_size:
            raise ValueError("JPEG verification failed.")
        output.load()


class PillowHeicConverter:
    """
    HEIC primary-image exporter backed by Pillow and libheif.
    Normalize orientation and retain supported EXIF, ICC, and XMP metadata.
    """

    def __init__(self) -> None:
        """
        Register HEIF decoding support with Pillow for this process.
        """
        register_heif_opener()

    def convert(self, source: Path, destination: Path, quality: int) -> None:
        """
        Write an RGB JPEG to the supplied staging path and decode it for verification.
        Leave the source intact and raise on invalid input, encoding, or verification.
        """
        with Image.open(source) as image:
            if image.format != "HEIF":
                raise ValueError("Source is not a HEIC/HEIF image.")
            # The HEIF plugin opens the primary image, which need not be frame zero.
            _save_jpeg(image, destination, quality)


class PillowJpegOptimizer:
    """
    Inspect JPEG headers and re-encode using the HEIC export settings.
    Inspection does not decode pixels but may hydrate an online-only OneDrive file.
    """

    def __init__(self) -> None:
        """
        Build reference quantization tables using the quality-90 JPEG encoder.
        Keep the reference in memory without decoding any JPEG pixels.
        """
        with BytesIO() as buffer:
            with Image.new("RGB", (8, 8)) as reference:
                reference.save(buffer, format="JPEG", quality=90, subsampling=2)
            buffer.seek(0)
            with Image.open(buffer) as reference:
                self._quality_90_tables = reference.quantization

    def needs_optimization(self, source: Path) -> bool:
        """
        Match JPEGs unless they already use quality-90 tables and 4:2:0 sampling.
        Inspect headers without decoding pixels.
        """
        with Image.open(source) as image:
            if image.format != "JPEG":
                raise ValueError("Source is not a JPEG image.")
            return not (
                image.quantization == self._quality_90_tables
                and JpegImagePlugin.get_sampling(image) == 2
            )

    def convert(self, source: Path, destination: Path, quality: int) -> None:
        """
        Re-encode one JPEG into verified staging output with supported metadata.
        Preserve the source; reject CMYK to avoid retaining an incompatible profile.
        """
        with Image.open(source) as image:
            if image.format != "JPEG" or image.mode not in {"RGB", "L"}:
                raise ValueError("Source must be an RGB or grayscale JPEG image.")
            _save_jpeg(image, destination, quality)


class RecycleBin:
    """
    Recoverable source-file removal through Send2Trash's platform backend.
    """

    def move(self, source: Path) -> None:
        """
        Recycle the source file, propagating failures without permanently deleting it.
        """
        send2trash(str(source))

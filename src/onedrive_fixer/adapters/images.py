"""
HEIC decoding and verified JPEG encoding using Pillow and libheif.
"""

from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from send2trash import send2trash


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


class RecycleBin:
    """
    Recoverable source-file removal through Send2Trash's platform backend.
    """

    def move(self, source: Path) -> None:
        """
        Recycle the source file, propagating failures without permanently deleting it.
        """
        send2trash(str(source))

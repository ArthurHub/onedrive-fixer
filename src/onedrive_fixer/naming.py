"""
Pure filename rules, independent of the filesystem and user interface.
"""

import re
from datetime import datetime

_PREFIX = re.compile(r"^(\d{8}_\d{6})(.*)$")


def corrected_name(name: str, created_at: datetime) -> str | None:
    """
    Replace a valid leading timestamp with the capture wall time, retaining the suffix.
    Separate attached milliseconds with an underscore; return None for invalid prefixes.
    """
    match = _PREFIX.fullmatch(name)
    if match is None:
        return None
    try:
        datetime.strptime(match[1], "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    suffix = match[2]
    if suffix and suffix[0].isdigit():
        suffix = "_" + suffix
    return created_at.strftime("%Y%m%d_%H%M%S") + suffix

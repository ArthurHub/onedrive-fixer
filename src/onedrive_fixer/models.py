"""
Immutable plans and results shared by every user interface.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    """
    Outcome of scanning or applying an operation to one file.
    READY means a change is planned; COMPLETE means it was applied successfully.
    """

    READY = "update_required"
    COMPLETE = "update_complete"
    UNCHANGED = "no_update_required"
    NOT_APPLE = "skipped_not_iphone"
    UNKNOWN_DATE = "skipped_date_unknown"
    CONFLICT = "conflict"
    ERROR = "error"


@dataclass(frozen=True)
class MediaMetadata:
    """
    Camera make and capture timestamp used to decide filename corrections.
    created_at retains the capture wall time; None means no usable date was found.
    """

    make: str = ""
    created_at: datetime | None = None


@dataclass(frozen=True)
class FileStamp:
    """
    File size, modification time, and identity captured for stale-plan checks.
    This detects common source changes without hashing the file contents.
    """

    size: int
    modified_ns: int
    inode: int

    @classmethod
    def read(cls, path: Path) -> FileStamp:
        """
        Capture a filesystem stamp without following a final symlink.
        Filesystem errors propagate to the caller.
        """
        stat = path.stat(follow_symlinks=False)
        return cls(stat.st_size, stat.st_mtime_ns, stat.st_ino)


@dataclass(frozen=True)
class FilePlan:
    """
    Immutable scan decision for a source file, including skips and failures.
    READY plans include a destination and source stamp for validation before apply.
    """

    source: Path
    status: Status
    destination: Path | None = None
    stamp: FileStamp | None = None
    message: str = ""


@dataclass(frozen=True)
class FileResult:
    """
    Outcome of validating or applying a plan, with an optional failure explanation.
    Successful dry runs retain READY status and do not modify the original plan.
    """

    plan: FilePlan
    status: Status
    message: str = ""
    dry_run: bool = False


@dataclass(frozen=True)
class Progress:
    """
    Progress notification delivered synchronously on the service caller's thread.
    current is a one-based file index within total files for the given phase.
    """

    phase: str
    current: int
    total: int
    path: Path

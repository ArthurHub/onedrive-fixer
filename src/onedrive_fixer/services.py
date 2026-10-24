"""
Scan, preview, and apply media operations without terminal or GUI dependencies.
"""

import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from onedrive_fixer.models import FilePlan, FileResult, FileStamp, Progress, Status
from onedrive_fixer.naming import corrected_name
from onedrive_fixer.ports import ImageConverter, JpegOptimizer, MetadataReader, Trash

MEDIA_EXTENSIONS = frozenset({".heic", ".jpg", ".jpeg", ".png", ".mp4", ".mov"})
type ProgressCallback = Callable[[Progress], None]


def _files(
    folder: Path,
    extensions: frozenset[str],
    *,
    name_filter: Callable[[str], bool] | None = None,
) -> list[Path]:
    """
    Collect matching regular files in deterministic folder traversal order.
    Skip descendant links and junctions; invalid roots and traversal errors propagate.
    """
    folder = folder.expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    files: list[Path] = []

    def raise_error(error: OSError) -> None:
        """
        Propagate walk errors so an incomplete traversal cannot appear successful.
        """
        raise error

    for current, directories, names in os.walk(folder, onerror=raise_error, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if not (Path(current) / name).is_symlink() and not (Path(current) / name).is_junction()
        )
        for name in sorted(names):
            candidate = Path(current) / name
            if (
                candidate.suffix.lower() in extensions
                and (name_filter is None or name_filter(name))
                and not candidate.is_symlink()
                and candidate.is_file()
            ):
                files.append(candidate)
    return files


def _notify(
    callback: ProgressCallback | None, phase: str, index: int, total: int, path: Path
) -> None:
    """
    Notify the optional observer on the caller's thread.
    Callback exceptions propagate; observers should not raise.
    """
    if callback:
        callback(Progress(phase, index, total, path))


def _collisions(plans: list[FilePlan]) -> list[FilePlan]:
    """
    Mark every READY plan sharing a destination as a conflict, ignoring case.
    Return new conflicting plans while preserving the original input objects.
    """
    counts: dict[str, int] = {}
    for plan in plans:
        if plan.status == Status.READY:
            key = str(plan.destination).casefold()
            counts[key] = counts.get(key, 0) + 1
    return [
        FilePlan(
            plan.source,
            Status.CONFLICT,
            plan.destination,
            plan.stamp,
            "Multiple source files have the same destination.",
        )
        if plan.status == Status.READY and counts[str(plan.destination).casefold()] > 1
        else plan
        for plan in plans
    ]


def _destination_exists(destination: Path) -> bool:
    """
    Check for a destination name using Windows-style case-insensitive matching.
    Include dangling links so publication cannot replace an existing directory entry.
    """
    if os.name == "nt":
        return destination.exists(follow_symlinks=False)
    return any(
        item.name.casefold() == destination.name.casefold() for item in destination.parent.iterdir()
    )


def scan_names(
    folder: Path, metadata: MetadataReader, progress: ProgressCallback | None = None
) -> list[FilePlan]:
    """
    Recursively plan Apple media filename corrections without writing files.
    Return per-file skips, errors, and conflicts; folder traversal errors propagate.
    """
    files = _files(folder, MEDIA_EXTENSIONS)
    plans: list[FilePlan] = []
    for index, source in enumerate(files, 1):
        try:
            stamp = FileStamp.read(source)
            tags = metadata.read(source)
            if tags.make.casefold() != "apple":
                plan = FilePlan(source, Status.NOT_APPLE)
            elif (
                tags.created_at is None
                or (name := corrected_name(source.name, tags.created_at)) is None
            ):
                plan = FilePlan(source, Status.UNKNOWN_DATE)
            elif source.name == name:
                plan = FilePlan(source, Status.UNCHANGED)
            else:
                destination = source.with_name(name)
                status = Status.CONFLICT if _destination_exists(destination) else Status.READY
                plan = FilePlan(
                    source,
                    status,
                    destination,
                    stamp,
                    "Destination already exists." if status == Status.CONFLICT else "",
                )
        except Exception as error:
            plan = FilePlan(source, Status.ERROR, message=str(error))
        plans.append(plan)
        _notify(progress, "scan", index, len(files), source)
    return _collisions(plans)


def scan_heic(folder: Path, progress: ProgressCallback | None = None) -> list[FilePlan]:
    """
    Recursively plan HEIC-to-JPEG conversions without decoding or writing images.
    Capture source stamps and report occupied or duplicate destinations as conflicts.
    """
    files = _files(folder, frozenset({".heic"}))
    plans: list[FilePlan] = []
    for index, source in enumerate(files, 1):
        try:
            destination = source.with_suffix(".jpeg")
            status = Status.CONFLICT if _destination_exists(destination) else Status.READY
            plan = FilePlan(
                source,
                status,
                destination,
                FileStamp.read(source),
                "Destination already exists." if status == Status.CONFLICT else "",
            )
        except OSError as error:
            plan = FilePlan(source, Status.ERROR, message=str(error))
        plans.append(plan)
        _notify(progress, "scan", index, len(files), source)
    return _collisions(plans)


def _validate(plan: FilePlan) -> Path:
    """
    Return the destination after checking plan completeness and current filesystem state.
    Raise for changed sources, cross-folder destinations, or occupied output names.
    """
    if plan.destination is None or plan.stamp is None:
        raise ValueError("Incomplete file plan.")
    if plan.source.is_symlink() or not plan.source.is_file():
        raise ValueError("Source is no longer a regular file; scan again.")
    if FileStamp.read(plan.source) != plan.stamp:
        raise ValueError("Source changed since the scan; scan again.")
    if plan.destination.parent != plan.source.parent:
        raise ValueError("Destination must be in the source folder.")
    if _destination_exists(plan.destination):
        raise FileExistsError(f"Destination already exists: {plan.destination}")
    return plan.destination


def _jpeg_backup(source: Path) -> Path:
    """
    Return the adjacent backup name, excluded from JPEG candidate scans.
    """
    return source.with_name(source.name + ".bak")


def _validate_jpeg(plan: FilePlan) -> Path:
    """
    Validate an in-place JPEG plan and reject an occupied backup name.
    Check the source stamp before encoding, backup, and replacement.
    """
    if plan.destination != plan.source or plan.stamp is None:
        raise ValueError("Incomplete in-place JPEG plan.")
    if plan.source.is_symlink() or not plan.source.is_file():
        raise ValueError("Source is no longer a regular file; scan again.")
    if FileStamp.read(plan.source) != plan.stamp:
        raise ValueError("Source changed since the scan; scan again.")
    backup = _jpeg_backup(plan.source)
    if _destination_exists(backup):
        raise FileExistsError(f"Backup already exists: {backup}")
    return plan.source


def scan_jpeg(
    folder: Path, optimizer: JpegOptimizer, progress: ProgressCallback | None = None
) -> list[FilePlan]:
    """
    Filter .jpeg names containing _iOS before opening only candidate JPEG headers.
    Skip quality-90 4:2:0 files; reads may hydrate cloud files.
    """
    files = _files(folder, frozenset({".jpeg"}), name_filter=lambda name: "_ios" in name.casefold())
    plans: list[FilePlan] = []
    for index, source in enumerate(files, 1):
        try:
            stamp = FileStamp.read(source)
            if optimizer.needs_optimization(source):
                # Hydration can change filesystem identity; capture the hydrated stamp.
                stamp = FileStamp.read(source)
                backup = _jpeg_backup(source)
                status = Status.CONFLICT if _destination_exists(backup) else Status.READY
                message = (
                    f"Backup already exists: {backup}"
                    if status == Status.CONFLICT
                    else (
                        "JPEG differs from quality 90 with 4:2:0; "
                        f"{stamp.size:,} bytes. Backup: {backup}"
                    )
                )
                plan = FilePlan(source, status, source, stamp, message)
            else:
                plan = FilePlan(
                    source,
                    Status.UNCHANGED,
                    message="JPEG already uses quality 90 with 4:2:0; skipped.",
                )
        except Exception as error:
            plan = FilePlan(source, Status.ERROR, message=str(error))
        plans.append(plan)
        _notify(progress, "scan", index, len(files), source)
    return plans


def _publish(source: Path, destination: Path) -> None:
    """
    Move a file to its destination without overwriting an existing directory entry.
    Use Windows rename or exclusive POSIX hard-link creation followed by unlink.
    """
    if os.name == "nt":
        source.rename(destination)
    else:
        os.link(source, destination)
        source.unlink()


def _apply(
    plans: Iterable[FilePlan],
    operation: Callable[[FilePlan], FileResult | None],
    dry_run: bool,
    progress: ProgressCallback | None,
    validate: Callable[[FilePlan], Path] = _validate,
) -> list[FileResult]:
    """
    Validate READY plans and optionally execute the supplied operation for each file.
    Preserve scan plans and collect failures as results so the batch can continue.
    """
    plans = list(plans)
    results: list[FileResult] = []
    for index, plan in enumerate(plans, 1):
        if plan.status != Status.READY:
            result = FileResult(plan, plan.status, plan.message, dry_run)
        else:
            try:
                validate(plan)
                outcome = None if dry_run else operation(plan)
                result = outcome or FileResult(
                    plan,
                    Status.READY if dry_run else Status.COMPLETE,
                    message=plan.message,
                    dry_run=dry_run,
                )
            except FileExistsError as error:
                result = FileResult(plan, Status.CONFLICT, str(error), dry_run)
            except Exception as error:
                result = FileResult(plan, Status.ERROR, str(error), dry_run)
        results.append(result)
        _notify(progress, "preview" if dry_run else "apply", index, len(plans), plan.source)
    return results


def fix_names(
    plans: Iterable[FilePlan], *, dry_run: bool = True, progress: ProgressCallback | None = None
) -> list[FileResult]:
    """
    Preview or apply READY filename changes, defaulting to a read-only preview.
    Revalidate each source and destination; return outcomes without mutating plans.
    """

    def rename(plan: FilePlan) -> None:
        """
        Publish the validated source under its planned filename without replacement.
        """
        assert plan.destination is not None
        _publish(plan.source, plan.destination)

    return _apply(plans, rename, dry_run, progress)


def convert_heic(
    plans: Iterable[FilePlan],
    converter: ImageConverter,
    trash: Trash,
    *,
    dry_run: bool = True,
    keep_original: bool = False,
    quality: int = 90,
    progress: ProgressCallback | None = None,
) -> list[FileResult]:
    """
    Preview or convert READY HEIC plans with JPEG quality from 1 to 100 (default 90).
    Dry runs perform no encoding or recycling. Apply stages and verifies each JPEG
    before publication, then recycles the source unless keep_original is set.
    Conversion failures preserve the source; recycling failures retain both files.
    """
    if not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be between 1 and 100.")

    def convert(plan: FilePlan) -> None:
        """
        Stage, verify, and publish one JPEG before optionally recycling its source.
        Clean up staging output and report recycling failures with the saved JPEG path.
        """
        assert plan.destination is not None
        descriptor, filename = tempfile.mkstemp(
            prefix=".onedrive-fixer-", suffix=".jpeg", dir=plan.source.parent
        )
        os.close(descriptor)
        temporary = Path(filename)
        try:
            converter.convert(plan.source, temporary, quality)
            if temporary.stat().st_size == 0:
                raise ValueError("Converter produced an empty JPEG.")
            _validate(plan)
            _publish(temporary, plan.destination)
        finally:
            temporary.unlink(missing_ok=True)
        if not keep_original:
            try:
                trash.move(plan.source)
            except Exception as error:
                raise RuntimeError(
                    f"JPEG saved at {plan.destination}; original could not be recycled: {error}"
                ) from error

    return _apply(plans, convert, dry_run, progress)


def optimize_jpeg(
    plans: Iterable[FilePlan],
    optimizer: JpegOptimizer,
    trash: Trash,
    *,
    dry_run: bool = True,
    keep_original: bool = False,
    quality: int = 90,
    progress: ProgressCallback | None = None,
) -> list[FileResult]:
    """
    Preview or replace eligible JPEGs only when verified output is smaller.
    Back up originals before replacement, keeping or recycling them after success.
    """
    if not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be between 1 and 100.")

    def optimize(plan: FilePlan) -> FileResult:
        """
        Stage a smaller JPEG, create an exclusive backup, then replace the source.
        Preserve the original or its backup if conversion or publication fails.
        """
        assert plan.stamp is not None
        if not optimizer.needs_optimization(plan.source):
            raise ValueError("Source already uses quality 90 with 4:2:0; scan again.")
        descriptor, filename = tempfile.mkstemp(
            prefix=".onedrive-fixer-", suffix=".jpeg", dir=plan.source.parent
        )
        os.close(descriptor)
        temporary = Path(filename)
        backup = _jpeg_backup(plan.source)
        try:
            optimizer.convert(plan.source, temporary, quality)
            new_size = temporary.stat().st_size
            if new_size == 0:
                raise ValueError("Optimizer produced an empty JPEG.")
            _validate_jpeg(plan)
            if new_size >= plan.stamp.size:
                return FileResult(
                    plan, Status.UNCHANGED, "Re-encoded JPEG is not smaller; kept original."
                )
            with backup.open("xb") as saved:
                try:
                    with plan.source.open("rb") as original:
                        shutil.copyfileobj(original, saved)
                    saved.flush()
                    os.fsync(saved.fileno())
                except BaseException:
                    saved.close()
                    backup.unlink()
                    raise
            # The backup now exists, so validate the source stamp directly.
            if plan.source.is_symlink() or FileStamp.read(plan.source) != plan.stamp:
                raise ValueError(f"Source changed before replacement; backup kept at {backup}.")
            try:
                temporary.replace(plan.source)
            except OSError as error:
                raise RuntimeError(
                    f"JPEG replacement failed; original backup kept at {backup}: {error}"
                ) from error
        finally:
            temporary.unlink(missing_ok=True)
        message = (
            f"Reduced {plan.stamp.size:,} to {new_size:,} bytes; "
            f"saved {plan.stamp.size - new_size:,} bytes."
        )
        if keep_original:
            message += f" Original kept at {backup}."
        else:
            try:
                trash.move(backup)
            except Exception as error:
                return FileResult(
                    plan,
                    Status.ERROR,
                    f"JPEG optimized at {plan.source}; "
                    f"original backup could not be recycled: {backup}: {error}",
                    bytes_saved=plan.stamp.size - new_size,
                )
        return FileResult(plan, Status.COMPLETE, message, bytes_saved=plan.stamp.size - new_size)

    return [
        replace(result, bytes_saved=0) if result.bytes_saved is None else result
        for result in _apply(plans, optimize, dry_run, progress, _validate_jpeg)
    ]

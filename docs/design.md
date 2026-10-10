# OneDrive Fixer: Python migration design

## Goal and scope

Migrate `web-dev-playground/projects/onedrive-fixer` from TypeScript to Python 3.14 while preserving its two workflows: correcting OneDrive's Apple media filenames and converting HEIC photos to JPEG. Deliver an interactive command interface and scriptable commands. A graphical interface is a future presentation adapter, not part of this migration.

The reference `F4VR-ImmersiveFlashlight` project separates source, documentation, external dependencies, generated build output, and development configuration. This project follows that organization with Python packaging conventions: a `src` package, `tests`, `docs`, and `pyproject.toml`. CMake, C++ headers, vcpkg, plugin data, and vendored dependency directories have no useful equivalents here and are not copied.

## Existing tool and compatibility

The original code has a menu with name fixing, HEIC conversion, and exit. It recursively scans folders, displays progress/counts, offers apply/dry-run/cancel, and caches three recent folders in a temporary JSON file.

| Behavior | Python implementation |
| --- | --- |
| Name scan extensions | `.heic`, `.jpg`, `.jpeg`, `.png`, `.mp4`, `.mov`; case insensitive |
| Eligible camera | `Make` equals `Apple`, case insensitive; as before, this can include other Apple devices |
| Timestamp source | EXIF `DateTimeDigitized` or video `creation_time`, corresponding to the former `CreateDate`; no filesystem-time fallback |
| Filename format | Leading `YYYYMMDD_HHMMSS`, followed by the original suffix and extension |
| Milliseconds | Insert `_` before a suffix starting with a digit |
| Missing metadata or filename date | Report a skipped/unknown-date result |
| Already-correct filename | Report unchanged |
| Conversion scan | Recursively find `.heic`, case insensitive |
| JPEG output | Same folder/stem, `.jpeg` extension, default quality 100 |
| Original after conversion | Recycle after verified JPEG publication; `--keep-original` is also available |
| Recent folders | Three most recent unique paths; tolerate malformed cache and read the old cache if needed |
| User interaction | Interactive menu, progress, preview, confirmation, summary; also scriptable subcommands |

The original converter's comment says quality 0.92, but its executed code uses 1.0; the port follows the code. It also writes and then trashes the generated JPEG during dry runs. The port deliberately makes dry runs read-only. Consequently, dry runs do not validate decoding. An apply operation validates decoding before it can recycle a source.

The original filename regex searches the entire path and then slices the basename as though its date begins at character zero. The port requires a valid leading timestamp in the basename. An unrelated date in a folder or an invalid date cannot accidentally trigger renaming.

## Timezone rules

For EXIF photo timestamps, use the stored capture-local wall clock. An attached offset must not cause conversion into the current machine's timezone. Pure filename generation formats the datetime's own calendar fields.

QuickTime integer creation timestamps are UTC. PyAV exposes these through the container's `creation_time` tag. For `.mov` and `.mp4`, when `com.apple.quicktime.creationdate` includes an offset, convert the creation timestamp to that fixed capture offset. For example, `2024-11-12T04:43:49Z` with a capture offset of `-08:00` becomes `20241111_204349`. The current computer's timezone is never used. FFmpeg can join duplicate timestamp tags with semicolons; accept matching values and treat conflicting or invalid creation timestamps as unknown.

If a video lacks a valid explicit capture offset, retain the container creation timestamp's wall time, normally UTC; the capture-local timezone cannot be reliably recovered. Do not infer a timezone from the current machine. This is a documented limitation: the former `exiftool-vendored` library has additional timezone inference heuristics that are not reproduced. There is no GPS-based timezone database or geolocation dependency in this port.

References: [ExifTool QuickTime date rules](https://exiftool.org/TagNames/QuickTime.html), [ExifTool date formatting](https://exiftool.org/filename.html).

## Structure and dependencies

```text
onedrive-fixer/
  pyproject.toml              Package, console command, dependencies, tooling
  uv.lock                    Reproducible dependency resolution
  .python-version            Python 3.14
  README.md                  Setup and user commands
  docs/
    design.md                Architecture and migration decisions
    development.md           Development and verification
  src/onedrive_fixer/
    __init__.py
    __main__.py              python -m entry point
    models.py                Immutable plans, outcomes, metadata, progress
    naming.py                Pure timestamp/name rules
    ports.py                 MetadataReader, ImageConverter, Trash protocols
    services.py              Recursive scan, conflict checks, apply operations
    adapters/
      metadata.py            Pillow/PyAV metadata extraction
      images.py              HEIC/JPEG codec and Recycle Bin adapters
      history.py             Recent-folder JSON persistence
    ui/
      cli.py                 Menu, argparse, presentation, composition
  tests/                     Unit, filesystem, codec, CLI, optional legacy checks
```

The dependency direction is `ui -> services -> models/naming/ports`. Infrastructure implements the protocols; the CLI constructs and injects concrete adapters. Services import neither the CLI nor Pillow, PyAV, prompts, or preferences. They use `pathlib`/`os` for local filesystem operations. A generic virtual filesystem abstraction would add complexity without a current consumer.

Use standard-library `argparse` for CLI parsing, dataclasses for typed values, and protocols for external operations. Use [Pillow](https://pillow.readthedocs.io/en/stable/installation/python-support.html) and [pillow-heif](https://pypi.org/project/pillow-heif/) for photo metadata, HEIC decoding, and JPEG encoding; [PyAV](https://pyav.basswood.io/docs/stable/overview/installation.html) for video metadata; and [Send2Trash](https://pypi.org/project/Send2Trash/) for reversible source removal. Standard wheels bundle native codec libraries, including FFmpeg for PyAV. There is no external executable installation, PATH lookup, or metadata subprocess. Dependencies are resolved and tested under Python 3.14.

`MediaMetadataReader` opens each photo with Pillow and reads `Make` plus the EXIF sub-IFD's `DateTimeDigitized`, the field ExifTool calls `CreateDate`. A valid `OffsetTimeDigitized` is retained without changing wall time. It opens MOV/MP4 containers with PyAV to read `creation_time`, `com.apple.quicktime.make` (or `make`), and `com.apple.quicktime.creationdate`. Video frames are not decoded, and the container is closed after metadata extraction. PyAV is imported only when a video is read.

This adapter covers the fields used by the original samples, rather than all metadata layouts supported by ExifTool. XMP-only dates, vendor maker-note fields, and metadata unavailable through Pillow or FFmpeg do not receive ExifTool's broader fallbacks. Missing dates are skipped and parsing failures are reported. Additional backends can still implement the existing `MetadataReader` protocol without changing services or the UI.

## Service contract and GUI reuse

1. `scan_names(folder, metadata, progress)` or `scan_heic(folder, progress)` returns immutable `FilePlan` objects. Each contains a source, optional destination, status, optional source stamp, and diagnostic text.
2. A UI displays the plans and obtains a user's choice. The services never prompt or print.
3. `fix_names(plans, dry_run=...)` or `convert_heic(plans, converter, trash, ...)` returns new `FileResult` objects. Scan plans are never mutated, so a preview does not consume the subsequent apply operation.
4. An optional progress callback receives typed `Progress` values during scan and apply. Per-file failures become results and processing continues.

A future GUI can run the same synchronous services on a worker thread, queue progress events to the UI thread, and render status rows. It must keep confirmation and preference handling in its own presentation layer. Callbacks run on the caller's thread; they are notifications and should not raise. Cancellation, parallel execution, and persistent job queues are future work. The CLI's JSON presentation is for scripts; it is not the core API.

## File operation safeguards

- Scanning and preview never write media. Interactive path history is a separate preference write.
- Existing destination names are checked case insensitively, including dangling links. Duplicate planned destinations are marked as conflicts for every affected source.
- Apply rechecks the source size, modification time in nanoseconds, and file identity, plus destination availability. A changed or missing source requires another scan. This is a practical stale-plan check, not a cryptographic content guarantee.
- Scanning skips descendant symlinks and Windows junctions. The explicitly selected root may itself resolve through a link.
- Publication never overwrites an existing destination. Windows rename enforces this atomically. POSIX uses exclusive hard-link creation followed by removal of the old/staging name. Filesystems without hard-link support report an error rather than falling back to an overwriting rename.
- Conversion stages the output beside the source on the same filesystem. The image adapter normalizes orientation, produces RGB JPEG, retains supported EXIF/ICC/XMP, and decodes the result to verify it. The service publishes the staged file before recycling the HEIC. Failed or interrupted conversion may leave a temporary file, but does not recycle an unconverted original.
- If recycling fails, report an error that names the saved JPEG and keep both files. Do not delete a successful JPEG to hide the failure. A later scan sees a conflict, leaving cleanup to the user.
- There is no whole-batch rollback: each file is independent. Recycle Bin operations are recoverable through the operating system; renames can be reversed manually.

Concurrent edits by another process between checks and the final operation cannot be completely eliminated with portable path APIs. Avoid editing or syncing the same files during apply. Destination creation is protected against concurrent overwrite; source-content coordination with OneDrive is outside this tool's scope.

JPEG is lossy, even at quality 100. Conversion exports the primary HEIC image to 8-bit RGB; auxiliary images, depth data, animation, and HDR capabilities are not preserved. Metadata support is best effort rather than complete container fidelity. Users needing the HEIC container should choose `--keep-original`.

## Verification and delivery

Use pytest for pure naming rules, timestamp-offset behavior, actual filesystem operations, source-change checks, collision handling, per-file failure continuation, JPEG decoding/metadata, recycle failure, dry-run immutability, menu/history, CLI exit codes, and optional read-only legacy media parity. Generate small HEIC fixtures during tests; do not copy personal media into this repository.

Use Ruff for lint/format validation and build a wheel and source distribution. Check the installed console entry point and run on CPython 3.14. Local runtime downloads, virtual environments, build output, and caches are ignored. No git initialization, commit, remote push, GUI framework, or CI deployment is part of this migration.

"""
Interactive menu and scriptable command-line entry point.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from onedrive_fixer.adapters.history import RecentFolders
from onedrive_fixer.adapters.metadata import MediaMetadataReader
from onedrive_fixer.models import FilePlan, FileResult, Progress, Status
from onedrive_fixer.services import convert_heic, fix_names, scan_heic, scan_names


def _parser() -> argparse.ArgumentParser:
    """
    Build menu options and subcommands that preview changes unless --apply is given.
    """
    parser = argparse.ArgumentParser(
        description="Fix OneDrive media names or convert HEIC to JPEG."
    )
    commands = parser.add_subparsers(dest="command")
    for command, help_text in [
        ("fix-names", "Fix Apple media filename timestamps"),
        ("convert-heic", "Convert HEIC to JPEG and recycle originals"),
    ]:
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument("folder", type=Path)
        mode = sub.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true", help="Apply the previewed operations")
        mode.add_argument("--dry-run", action="store_true", help="Preview only (the default)")
        sub.add_argument(
            "--yes", "-y", action="store_true", help="Confirm --apply without a prompt"
        )
        sub.add_argument("--json", action="store_true", help="Print machine-readable results")
        if command == "convert-heic":
            sub.add_argument("--keep-original", action="store_true", help="Keep HEIC originals")
            sub.add_argument(
                "--quality",
                type=int,
                choices=range(1, 101),
                default=100,
                metavar="1-100",
                help="JPEG quality (default: 100)",
            )
    return parser


def _progress(event: Progress) -> None:
    """
    Render file progress to stderr so stdout remains available for command results.
    """
    print(f"{event.phase} {event.current}/{event.total}: {event.path.name}", file=sys.stderr)


def _scan(command: str, folder: Path, quiet: bool = False) -> list[FilePlan]:
    """
    Select the scan service and construct its metadata adapter when required.
    Suppress progress notifications for machine-readable output.
    """
    callback = None if quiet else _progress
    if command == "fix-names":
        return scan_names(folder, MediaMetadataReader(), callback)
    return scan_heic(folder, callback)


def _apply(
    command: str,
    plans: list[FilePlan],
    *,
    dry_run: bool,
    keep_original: bool = False,
    quality: int = 100,
    quiet: bool = False,
) -> list[FileResult]:
    """
    Run the selected operation with CLI options and optional terminal progress.
    Preview through shared plan validation without loading the native image backend.
    """
    callback = None if quiet else _progress
    if command == "fix-names" or dry_run:
        return fix_names(plans, dry_run=dry_run, progress=callback)
    # A preview does not require loading the native HEIC library.
    from onedrive_fixer.adapters.images import PillowHeicConverter, RecycleBin

    return convert_heic(
        plans,
        PillowHeicConverter(),
        RecycleBin(),
        dry_run=dry_run,
        keep_original=keep_original,
        quality=quality,
        progress=callback,
    )


def _show(results: list[FileResult], as_json: bool = False) -> None:
    """
    Write structured JSON or human-readable file outcomes and counts to stdout.
    """
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "source": str(result.plan.source),
                        "destination": str(result.plan.destination)
                        if result.plan.destination
                        else None,
                        "status": result.status.value,
                        "message": result.message,
                        "dry_run": result.dry_run,
                    }
                    for result in results
                ],
                indent=2,
            )
        )
        return
    for result in results:
        destination = f" -> {result.plan.destination.name}" if result.plan.destination else ""
        detail = f" ({result.message})" if result.message else ""
        print(f"{result.status.value}: {result.plan.source}{destination}{detail}")
    counts = Counter(result.status.value for result in results)
    print(
        f"{len(results)} media files; "
        + ", ".join(f"{key}: {value}" for key, value in counts.items())
    )


def _failed(results: list[FileResult]) -> bool:
    """
    Return whether any file error or destination conflict requires a failure exit code.
    """
    return any(result.status in {Status.ERROR, Status.CONFLICT} for result in results)


def _confirm(message: str) -> bool:
    """
    Accept only an explicit yes response, treating empty input as cancellation.
    """
    return input(f"{message} [y/N] ").strip().lower() in {"y", "yes"}


def _choose_folder(history: RecentFolders) -> Path:
    """
    Prompt for an existing folder or recent-folder selection and remember its path.
    Report preference-write errors without preventing the chosen operation.
    """
    recent = history.load()
    for index, folder in enumerate(recent, 1):
        print(f"{index}. {folder}")
    while True:
        value = input("Enter a folder path or recent-folder number: ").strip().strip('"')
        if value.isdigit() and 1 <= int(value) <= len(recent):
            value = recent[int(value) - 1]
        folder = Path(value).expanduser()
        if value and folder.is_dir():
            folder = folder.resolve()
            try:
                history.remember(folder)
            except OSError as error:
                print(f"Could not save recent folders: {error}", file=sys.stderr)
            return folder
        print("The specified folder does not exist.")


def _menu() -> int:
    """
    Run the interactive preview, confirmation, and apply loop until the user exits.
    Report operational failures and return to the menu so another task can be chosen.
    """
    history = RecentFolders()
    while True:
        print("\n1. Fix OneDrive media file names\n2. Convert HEIC photos to JPEG\n3. Exit")
        choice = input("Select an option: ").strip()
        if choice == "3":
            return 0
        if choice not in {"1", "2"}:
            continue
        command = "fix-names" if choice == "1" else "convert-heic"
        try:
            folder = _choose_folder(history)
            plans = _scan(command, folder)
            preview = _apply(command, plans, dry_run=True)
            _show(preview)
            if not any(result.status == Status.READY for result in preview):
                continue
            keep_original = False
            if command == "convert-heic":
                keep_original = _confirm("Keep the original HEIC files?")
            action = input("1. Apply  2. Dry run  3. Cancel [3]: ").strip()
            if action == "2":
                _show(_apply(command, plans, dry_run=True))
                if not _confirm("Apply for real?"):
                    continue
            elif action != "1":
                continue
            _show(_apply(command, plans, dry_run=False, keep_original=keep_original))
        except (OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """
    Run the menu or parsed subcommand and return its process exit code.
    Use process arguments when argv is None; argparse exits with code 2 for bad usage.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            if not sys.stdin.isatty():
                parser.error("Specify a command and folder when running without a terminal.")
            return _menu()
        if args.apply and not args.yes and (args.json or not sys.stdin.isatty()):
            parser.error("Noninteractive --apply requires --yes.")
        plans = _scan(args.command, args.folder, args.json)
        if args.apply and not args.yes:
            _show(_apply(args.command, plans, dry_run=True))
            if not _confirm("Apply these operations?"):
                return 0
        results = _apply(
            args.command,
            plans,
            dry_run=not args.apply,
            keep_original=getattr(args, "keep_original", False),
            quality=getattr(args, "quality", 100),
            quiet=args.json,
        )
        _show(results, args.json)
        return 1 if _failed(results) else 0
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except EOFError, KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130

"""
Interactive menu and scriptable command-line entry point.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import questionary

from onedrive_fixer.adapters.history import RecentFolders
from onedrive_fixer.adapters.metadata import MediaMetadataReader
from onedrive_fixer.models import FilePlan, FileResult, Status
from onedrive_fixer.services import convert_heic, fix_names, scan_heic, scan_names
from onedrive_fixer.ui.terminal import (
    STATUS_LABELS,
    confirm,
    prompt_folder,
    report_progress,
    select,
    show_error,
    show_results,
    show_welcome,
)


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
                default=90,
                metavar="1-100",
                help="JPEG quality (default: 90)",
            )
    return parser


def _scan(command: str, folder: Path, quiet: bool = False) -> list[FilePlan]:
    """
    Select the scan service and construct its metadata adapter when required.
    Suppress progress notifications for machine-readable output.
    """
    with report_progress(quiet=quiet) as callback:
        if command == "fix-names":
            return scan_names(folder, MediaMetadataReader(), callback)
        return scan_heic(folder, callback)


def _apply(
    command: str,
    plans: list[FilePlan],
    *,
    dry_run: bool,
    keep_original: bool = False,
    quality: int = 90,
    quiet: bool = False,
) -> list[FileResult]:
    """
    Run the selected operation with CLI options and optional terminal progress.
    Preview through shared plan validation without loading the native image backend.
    """
    with report_progress(quiet=quiet) as callback:
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
    show_results(results)


def _failed(results: list[FileResult]) -> bool:
    """
    Return whether any file error or destination conflict requires a failure exit code.
    """
    return any(result.status in {Status.ERROR, Status.CONFLICT} for result in results)


def _review(
    results: list[FileResult], *, allow_apply: bool = False, allow_preview: bool = False
) -> str:
    """
    Show counts and let the user print individual categories without changing files.
    Offer apply/preview actions only when requested and changes are ready.
    """
    _show(results)
    counts = Counter(result.status for result in results)
    choices = [
        questionary.Choice(f"Print {label} files ({counts[status]})", value=status.value)
        for status, (label, _) in STATUS_LABELS.items()
        if counts[status]
    ]
    if allow_apply and counts[Status.READY]:
        choices.append(questionary.Choice("Apply changes", value="apply"))
        if allow_preview:
            choices.append(questionary.Choice("Preview again", value="preview"))
    choices.append(questionary.Choice("Cancel" if allow_apply else "Done", value="cancel"))
    while True:
        choice = select("Review results:", choices, default="cancel")
        if choice in {"apply", "preview", "cancel"}:
            return choice
        show_results([result for result in results if result.status.value == choice], details=True)


def _choose_folder(history: RecentFolders) -> Path | None:
    """
    Select a recent folder or enter a path with completion; Back returns None.
    Report preference-write errors without preventing the chosen operation.
    """
    recent = history.load()
    while True:
        choice = select(
            "Choose a folder:",
            [questionary.Choice(folder, value=str(index)) for index, folder in enumerate(recent)]
            + [
                questionary.Choice("Enter another folder…", value="new"),
                questionary.Choice("Back to main menu", value="back"),
            ],
        )
        if choice == "back":
            return None
        value = prompt_folder() if choice == "new" else recent[int(choice)]
        value = value.strip().strip('"')
        folder = Path(value).expanduser()
        if value and folder.is_dir():
            folder = folder.resolve()
            try:
                history.remember(folder)
            except OSError as error:
                show_error(f"Could not save recent folders: {error}")
            return folder
        show_error("The specified folder does not exist.")


def _menu() -> int:
    """
    Run the interactive preview, confirmation, and apply loop until the user exits.
    Report operational failures and return to the menu so another task can be chosen.
    """
    history = RecentFolders()
    show_welcome()
    while True:
        command = select(
            "What would you like to do?",
            [
                questionary.Choice("Fix OneDrive media filenames", value="fix-names"),
                questionary.Choice("Convert HEIC photos to JPEG", value="convert-heic"),
                questionary.Choice("Exit", value="exit"),
            ],
        )
        if command == "exit":
            return 0
        try:
            folder = _choose_folder(history)
            if folder is None:
                continue
            plans = _scan(command, folder)
            preview = _apply(command, plans, dry_run=True)
            while (action := _review(preview, allow_apply=True, allow_preview=True)) == "preview":
                preview = _apply(command, plans, dry_run=True)
            if action != "apply":
                continue
            keep_original = False
            if command == "convert-heic":
                keep_original = confirm(
                    "Keep the original HEIC files? (No moves them to Recycle Bin)"
                )
            _review(_apply(command, plans, dry_run=False, keep_original=keep_original))
        except (OSError, ValueError) as error:
            show_error(f"Error: {error}")


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
        if (
            args.apply
            and not args.yes
            and _review(_apply(args.command, plans, dry_run=True), allow_apply=True) != "apply"
        ):
            return 0
        results = _apply(
            args.command,
            plans,
            dry_run=not args.apply,
            keep_original=getattr(args, "keep_original", False),
            quality=getattr(args, "quality", 90),
            quiet=args.json,
        )
        if not args.json and not args.yes and sys.stdin.isatty() and sys.stdout.isatty():
            _review(results)
        else:
            _show(results, args.json)
        return 1 if _failed(results) else 0
    except (OSError, ValueError) as error:
        show_error(f"Error: {error}")
        return 1
    except EOFError, KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130

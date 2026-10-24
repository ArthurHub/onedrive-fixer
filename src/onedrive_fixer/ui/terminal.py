"""
Styled terminal prompts and result rendering, separate from media operations.
"""

from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, SpinnerColumn, TextColumn
from rich.progress import Progress as RichProgress
from rich.table import Table
from rich.text import Text

from onedrive_fixer.models import FileResult, Progress, Status

PROMPT_STYLE = questionary.Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("answer", "fg:cyan"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("instruction", "fg:ansibrightblack"),
    ]
)

STATUS_LABELS = {
    Status.READY: ("Ready", "cyan"),
    Status.COMPLETE: ("Complete", "green"),
    Status.UNCHANGED: ("Unchanged", "dim"),
    Status.NOT_APPLE: ("Not Apple", "dim"),
    Status.UNKNOWN_DATE: ("Unknown date", "yellow"),
    Status.CONFLICT: ("Conflict", "bold yellow"),
    Status.ERROR: ("Error", "bold red"),
}


def select(message: str, choices: list[questionary.Choice], *, default: str | None = None) -> str:
    """
    Select a choice with arrow keys and Enter, propagating cancellation to the CLI.
    All choices must carry string values.
    """
    return questionary.select(
        message,
        choices=choices,
        default=default,
        style=PROMPT_STYLE,
        instruction="(Use arrow keys and Enter; Ctrl+C to exit)",
    ).unsafe_ask()


def confirm(message: str) -> bool:
    """
    Require an explicit yes and Enter, with cancellation propagated to the CLI.
    """
    return questionary.confirm(
        message, default=False, auto_enter=False, style=PROMPT_STYLE
    ).unsafe_ask()


def prompt_folder() -> str:
    """
    Read a folder path with directory completion; validation belongs to the caller.
    """
    return questionary.path(
        "Folder path (Tab to complete):", only_directories=True, style=PROMPT_STYLE
    ).unsafe_ask()


def show_welcome() -> None:
    """
    Introduce the available operations and preview-first workflow.
    """
    Console().print(
        Panel(
            "Fix Apple media filenames, convert HEIC, or reduce converted JPEG sizes.\n"
            "Review a preview before applying changes.",
            title="OneDrive Fixer",
            border_style="cyan",
            expand=False,
        )
    )


def show_error(message: str) -> None:
    """
    Write a literal error or warning to stderr without interpreting path markup.
    """
    Console(stderr=True).print(Text(message, style="bold red"))


def show_jpeg_notice() -> None:
    """
    Explain cloud downloads and original handling before scanning JPEG candidates.
    """
    Console(stderr=True).print(
        "Only .jpeg filenames containing _iOS are opened (case-insensitive).\n"
        "Checking online-only candidates may download them, including during preview.\n"
        "JPEGs already using quality 90 with 4:2:0 color sampling are skipped.\n"
        "Apply replaces eligible JPEGs only when the verified output is smaller.\n"
        "Originals go to Recycle Bin unless kept as .jpeg.bak files."
    )


def show_results(results: list[FileResult], *, details: bool = False) -> None:
    """
    Show category counts, with literal file paths only when details are requested.
    Rich automatically uses plain output when stdout is redirected.
    """
    console = Console()
    if not results:
        console.print("No matching media files found.", style="dim")
        return
    title = "Preview — no files changed" if all(item.dry_run for item in results) else "Results"
    if details:
        table = Table(
            title=title,
            box=box.SIMPLE_HEAD,
            header_style="bold",
            expand=True,
        )
        table.add_column("Status", no_wrap=True)
        table.add_column("Source", overflow="fold", ratio=2)
        table.add_column("Destination", overflow="fold", ratio=2)
        table.add_column("Details", overflow="fold", ratio=1)
        for result in results:
            label, style = STATUS_LABELS[result.status]
            table.add_row(
                Text(label, style=style),
                Text(str(result.plan.source)),
                Text(str(result.plan.destination) if result.plan.destination else ""),
                Text(result.message),
            )
        console.print(table)
    else:
        console.print(title, style="bold")
    counts = Counter(result.status for result in results)
    summary = Text(f"{len(results)} media files; ")
    for index, (status, count) in enumerate(counts.items()):
        if index:
            summary.append(", ")
        label, style = STATUS_LABELS[status]
        summary.append(f"{label}: {count}", style=style)
    console.print(summary)
    if any(result.bytes_saved is not None and not result.dry_run for result in results):
        total_saved = sum(result.bytes_saved or 0 for result in results if not result.dry_run)
        console.print(f"Total saved: {total_saved / 1_000_000:,.2f} MB", style="green")


@contextmanager
def report_progress(*, quiet: bool = False) -> Generator[Callable[[Progress], None] | None]:
    """
    Provide a service callback using live terminal progress or plain stderr lines.
    Quiet operations emit nothing; the display always closes on errors or cancellation.
    """
    if quiet:
        yield None
        return
    console = Console(stderr=True)
    if not console.is_interactive:

        def plain(event: Progress) -> None:
            """
            Preserve readable progress when stderr is redirected.
            """
            console.print(Text(f"{event.phase} {event.current}/{event.total}: {event.path.name}"))

        yield plain
        return
    with RichProgress(
        SpinnerColumn(),
        TextColumn("{task.description}", markup=False),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("{task.fields[filename]}", markup=False),
        console=console,
        transient=True,
    ) as display:
        task = display.add_task("Finding media files", total=None, filename="")

        def update(event: Progress) -> None:
            """
            Update the active service phase and completed-file count.
            """
            display.update(
                task,
                description=event.phase.capitalize(),
                completed=event.current,
                total=event.total,
                filename=event.path.name,
            )

        yield update

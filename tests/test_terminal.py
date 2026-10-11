from io import StringIO
from pathlib import Path

import pytest
import questionary
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from onedrive_fixer.models import FilePlan, FileResult, Progress, Status
from onedrive_fixer.ui import terminal


@pytest.fixture
def keyboard(monkeypatch):
    with create_pipe_input() as input_stream:
        for name in ["select", "confirm"]:
            factory = getattr(questionary, name)

            def configured(*args, factory=factory, **kwargs):
                return factory(*args, input=input_stream, output=DummyOutput(), **kwargs)

            monkeypatch.setattr(questionary, name, configured)
        yield input_stream


def test_select_with_down_arrow_and_enter(keyboard):
    keyboard.send_text("\x1b[B\r")
    assert (
        terminal.select(
            "Choose:",
            [
                questionary.Choice("First", value="first"),
                questionary.Choice("Second", value="second"),
            ],
        )
        == "second"
    )


def test_enter_keeps_cancel_selected(keyboard):
    keyboard.send_text("\r")
    assert (
        terminal.select(
            "Apply?",
            [
                questionary.Choice("Apply", value="apply"),
                questionary.Choice("Cancel", value="cancel"),
            ],
            default="cancel",
        )
        == "cancel"
    )


def test_ctrl_c_propagates_from_questionary(keyboard):
    keyboard.send_text("\x03")
    with pytest.raises(KeyboardInterrupt):
        terminal.select("Choose:", [questionary.Choice("Exit", value="exit")])


def test_confirmation_defaults_to_no(keyboard):
    keyboard.send_text("\r")
    assert terminal.confirm("Apply?") is False


def test_redirected_results_and_progress_are_plain_and_paths_are_literal(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "180")
    path = Path("[red]photo.heic")
    plan = FilePlan(path, Status.CONFLICT, path.with_suffix(".jpeg"))
    terminal.show_results(
        [FileResult(plan, Status.CONFLICT, "Already exists", dry_run=True)], details=True
    )
    with terminal.report_progress() as callback:
        callback(Progress("scan", 1, 1, path))
    output = capsys.readouterr()
    assert "[red]photo.heic" in output.out
    assert "[red]photo.jpeg" in output.out
    assert "Conflict: 1" in output.out
    assert "no files changed" in output.out
    assert "scan 1/1: [red]photo.heic" in output.err
    assert "\x1b" not in output.out + output.err


def test_live_progress_handles_literal_names_and_cleans_up_on_failure(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = StringIO()
    console = Console(file=stream, force_terminal=True, width=120)
    monkeypatch.setattr(terminal, "Console", lambda **kwargs: console)
    with pytest.raises(ValueError), terminal.report_progress() as callback:
        callback(Progress("scan", 1, 1, Path("[red]photo.heic")))
        raise ValueError("scan failed")
    output = stream.getvalue()
    assert "[red]photo.heic" in output
    assert "\x1b[?25h" in output  # Restore the cursor when the progress display closes.


def test_jpeg_summary_totals_actual_reductions_in_mb(capsys):
    plan = FilePlan(Path("photo_iOS.jpeg"), Status.READY)
    terminal.show_results(
        [
            FileResult(plan, Status.COMPLETE, bytes_saved=1_250_000),
            FileResult(plan, Status.COMPLETE, bytes_saved=750_000),
            FileResult(plan, Status.ERROR, "Backup recycling failed", bytes_saved=500_000),
            FileResult(plan, Status.ERROR, "Encoding failed", bytes_saved=0),
            FileResult(plan, Status.UNCHANGED, bytes_saved=0),
            FileResult(plan, Status.READY, dry_run=True, bytes_saved=0),
        ]
    )
    output = capsys.readouterr().out
    assert output.rstrip().endswith("Total saved: 2.50 MB")


def test_jpeg_summary_shows_zero_when_no_files_were_reduced(capsys):
    plan = FilePlan(Path("photo_iOS.jpeg"), Status.UNCHANGED)
    terminal.show_results([FileResult(plan, Status.UNCHANGED, bytes_saved=0)])
    assert capsys.readouterr().out.rstrip().endswith("Total saved: 0.00 MB")


@pytest.mark.parametrize("dry_run, bytes_saved", [(True, 0), (False, None)])
def test_previews_and_other_operations_omit_savings(capsys, dry_run, bytes_saved):
    plan = FilePlan(Path("photo.jpeg"), Status.READY)
    terminal.show_results(
        [FileResult(plan, Status.READY, dry_run=dry_run, bytes_saved=bytes_saved)]
    )
    assert "Total saved:" not in capsys.readouterr().out

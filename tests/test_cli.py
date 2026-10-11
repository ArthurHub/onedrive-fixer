import json

import pytest

from onedrive_fixer.adapters.history import RecentFolders
from onedrive_fixer.models import FilePlan, FileResult, Status
from onedrive_fixer.ui.cli import main


@pytest.mark.parametrize("quality_args, luminance", [([], 3), (["--quality", "85"], 5)])
def test_json_preview_and_apply_with_keep_original(tmp_path, capsys, quality_args, luminance):
    from PIL import Image, JpegImagePlugin
    from pillow_heif import register_heif_opener

    register_heif_opener()
    source = tmp_path / "photo.heic"
    with Image.new("RGB", (16, 16), "blue") as image:
        image.save(source, format="HEIF")
    assert main(["convert-heic", str(tmp_path), "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "\x1b" not in output.out
    preview = json.loads(output.out)
    assert preview[0]["status"] == Status.READY.value
    assert preview[0]["dry_run"] is True
    assert list(tmp_path.iterdir()) == [source]
    assert (
        main(
            ["convert-heic", str(tmp_path), "--apply", "--yes", "--keep-original", "--json"]
            + quality_args
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied[0]["status"] == Status.COMPLETE.value
    assert source.exists() and source.with_suffix(".jpeg").exists()
    with Image.open(source.with_suffix(".jpeg")) as jpeg:
        assert JpegImagePlugin.get_sampling(jpeg) == 2
        assert jpeg.quantization[0][0] == luminance


def test_conflicts_set_failure_exit_code(tmp_path, capsys):
    (tmp_path / "photo.heic").touch()
    (tmp_path / "photo.jpeg").write_bytes(b"existing")
    assert main(["convert-heic", str(tmp_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["status"] == Status.CONFLICT.value


def test_fix_names_uses_library_metadata_without_external_processes(tmp_path, monkeypatch, capsys):
    from PIL import ExifTags, Image

    def unexpected_process(*args, **kwargs):
        pytest.fail("Metadata reading attempted to launch an external executable")

    monkeypatch.setattr("subprocess.Popen", unexpected_process)
    source = tmp_path / "20220101_100000.jpg"
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Apple"
    exif[ExifTags.IFD.Exif] = {ExifTags.Base.DateTimeDigitized: "2022:01:01 12:00:00"}
    with Image.new("RGB", (16, 16)) as image:
        image.save(source, exif=exif)
    assert main(["fix-names", str(tmp_path), "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview[0]["status"] == Status.READY.value
    assert preview[0]["destination"].endswith("20220101_120000.jpg")
    assert source.exists()
    assert main(["fix-names", str(tmp_path), "--apply", "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == Status.COMPLETE.value
    assert not source.exists()
    assert (tmp_path / "20220101_120000.jpg").exists()


def test_unattended_apply_requires_explicit_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        main(["convert-heic", str(tmp_path), "--apply"])
    assert error.value.code == 2


def test_invalid_folder_is_operational_failure(tmp_path, capsys):
    assert main(["convert-heic", str(tmp_path / "missing")]) == 1
    assert "Error:" in capsys.readouterr().err


def test_interactive_menu_preview_cancel_and_history(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "onedrive_fixer.adapters.history.tempfile.gettempdir", lambda: str(tmp_path)
    )
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "photo.heic").touch()
    history = RecentFolders(tmp_path / "history.json")
    monkeypatch.setattr("onedrive_fixer.ui.cli.RecentFolders", lambda: history)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["convert-heic", "new", "cancel", "exit"])

    def choose(message, choices, *, default=None):
        answer = next(answers)
        assert answer in [choice.value for choice in choices]
        if answer == "cancel":
            assert default == "cancel"
        return answer

    monkeypatch.setattr("onedrive_fixer.ui.cli.select", choose)
    monkeypatch.setattr("onedrive_fixer.ui.cli.prompt_folder", lambda: f'"{photos}"')
    monkeypatch.setattr("onedrive_fixer.ui.cli.confirm", lambda _: False)
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "Ready: 1" in output
    assert "photo.heic" not in output
    assert history.load() == [str(photos)]
    assert list(photos.iterdir()) == [photos / "photo.heic"]


def test_history_keeps_five_unique_folders_and_handles_corruption(tmp_path):
    history = RecentFolders(tmp_path / "history.json")
    history.storage.write_text("not valid JSON", encoding="utf-8")
    assert history.load() == []
    for name in ["a", "b", "c", "d", "e", "f", "b"]:
        history.remember(tmp_path / name)
    expected = [str(tmp_path / name) for name in ["b", "f", "e", "d", "c"]]
    assert history.load() == expected
    assert json.loads(history.storage.read_text(encoding="utf-8"))["lastUsedPaths"] == expected
    history.storage.write_text('{"lastUsedPaths": "incorrect type"}', encoding="utf-8")
    assert history.load() == []


def test_folder_picker_offers_five_paths_and_remembers_selection(tmp_path, monkeypatch):
    from onedrive_fixer.ui.cli import _choose_folder

    history = RecentFolders(tmp_path / "history.json")
    folders = [tmp_path / str(index) for index in range(5)]
    for folder in folders:
        folder.mkdir()
        history.remember(folder)

    def choose(message, choices):
        assert [choice.title for choice in choices[:5]] == [str(path) for path in reversed(folders)]
        return choices[4].value

    monkeypatch.setattr("onedrive_fixer.ui.cli.select", choose)
    assert _choose_folder(history) == folders[0]
    assert history.load() == [str(folders[0]), *map(str, reversed(folders[1:]))]


def test_missing_recent_folder_can_return_to_menu(tmp_path, monkeypatch, capsys):
    from onedrive_fixer.ui.cli import _choose_folder

    history = RecentFolders(tmp_path / "history.json")
    history.remember(tmp_path / "missing")
    answers = iter(["0", "back"])
    monkeypatch.setattr("onedrive_fixer.ui.cli.select", lambda *args: next(answers))
    assert _choose_folder(history) is None
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.parametrize("apply_changes", [False, True])
def test_preview_again_requires_apply_selection_before_conversion(
    tmp_path, monkeypatch, capsys, apply_changes
):
    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    source = tmp_path / "photo.heic"
    with Image.new("RGB", (16, 16), "blue") as image:
        image.save(source, format="HEIF")
    history = RecentFolders(tmp_path / "history.json")
    monkeypatch.setattr("onedrive_fixer.ui.cli.RecentFolders", lambda: history)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(
        ["convert-heic", "new", "preview", "apply", "cancel", "exit"]
        if apply_changes
        else ["convert-heic", "new", "preview", "cancel", "exit"]
    )
    monkeypatch.setattr("onedrive_fixer.ui.cli.select", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr("onedrive_fixer.ui.cli.prompt_folder", lambda: str(tmp_path))
    monkeypatch.setattr("onedrive_fixer.ui.cli.confirm", lambda _: True)
    assert main([]) == 0
    assert source.exists()
    assert source.with_suffix(".jpeg").exists() is apply_changes
    assert "Preview" in capsys.readouterr().out


@pytest.mark.parametrize("status", list(Status))
def test_review_prints_only_requested_category(tmp_path, monkeypatch, capsys, status):
    from onedrive_fixer.ui.cli import _review

    results = [
        FileResult(FilePlan(tmp_path / f"category-{index}.heic", item), item, dry_run=True)
        for index, item in enumerate(Status)
    ]
    selected = next(result for result in results if result.status == status)
    prompts = 0

    def choose(message, choices, *, default):
        nonlocal prompts
        output = capsys.readouterr().out
        assert default == "cancel"
        assert {item.value for item in choices} == {item.value for item in Status} | {"cancel"}
        if prompts == 0:
            assert "7 media files" in output
            assert not any(result.plan.source.name in output for result in results)
            prompts += 1
            return status.value
        assert selected.plan.source.name in output
        assert not any(
            result.plan.source.name in output for result in results if result.status != status
        )
        return "cancel"

    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr("onedrive_fixer.ui.cli.select", choose)
    assert _review(results) == "cancel"


def test_menu_allows_category_review_when_no_changes_are_ready(tmp_path, monkeypatch, capsys):
    history = RecentFolders(tmp_path / "history.json")
    history.remember(tmp_path)
    source = tmp_path / "unchanged.jpg"
    monkeypatch.setattr("onedrive_fixer.ui.cli.RecentFolders", lambda: history)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "onedrive_fixer.ui.cli._scan", lambda *args: [FilePlan(source, Status.UNCHANGED)]
    )
    answers = iter(["fix-names", "0", Status.UNCHANGED.value, "cancel", "exit"])

    def choose(message, choices, **kwargs):
        assert "apply" not in [choice.value for choice in choices]
        assert "preview" not in [choice.value for choice in choices]
        return next(answers)

    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr("onedrive_fixer.ui.cli.select", choose)
    assert main([]) == 0
    assert "unchanged.jpg" in capsys.readouterr().out
    assert not source.exists()


def test_redirected_subcommand_shows_summary_without_prompting(tmp_path, monkeypatch, capsys):
    source = tmp_path / "photo.heic"
    source.touch()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["convert-heic", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert "Ready: 1" in output.out
    assert "photo.heic" not in output.out
    assert source.exists() and not source.with_suffix(".jpeg").exists()


def test_yes_skips_result_review_even_in_a_terminal(tmp_path, monkeypatch, capsys):
    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    source = tmp_path / "photo.heic"
    with Image.new("RGB", (16, 16), "blue") as image:
        image.save(source, format="HEIF")

    def unexpected_prompt(*args, **kwargs):
        pytest.fail("--yes must not prompt for result review")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("onedrive_fixer.ui.cli.select", unexpected_prompt)
    assert main(["convert-heic", str(tmp_path), "--apply", "--yes", "--keep-original"]) == 0
    assert source.exists() and source.with_suffix(".jpeg").exists()
    assert "Complete: 1" in capsys.readouterr().out


@pytest.mark.parametrize("error", [KeyboardInterrupt, EOFError])
def test_prompt_cancellation_exits_without_changes(tmp_path, monkeypatch, capsys, error):
    def cancel(*args, **kwargs):
        raise error

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("onedrive_fixer.ui.cli.select", cancel)
    assert main([]) == 130
    assert "Cancelled." in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []

import json

import pytest

from onedrive_fixer.adapters.history import RecentFolders
from onedrive_fixer.models import Status
from onedrive_fixer.ui.cli import main


def test_json_preview_and_apply_with_keep_original(tmp_path, capsys):
    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    source = tmp_path / "photo.heic"
    with Image.new("RGB", (16, 16), "blue") as image:
        image.save(source, format="HEIF")
    assert main(["convert-heic", str(tmp_path), "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview[0]["status"] == Status.READY.value
    assert preview[0]["dry_run"] is True
    assert list(tmp_path.iterdir()) == [source]
    assert (
        main(["convert-heic", str(tmp_path), "--apply", "--yes", "--keep-original", "--json"]) == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied[0]["status"] == Status.COMPLETE.value
    assert source.exists() and source.with_suffix(".jpeg").exists()


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
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "photo.heic").touch()
    history = RecentFolders(tmp_path / "history.json")
    monkeypatch.setattr("onedrive_fixer.ui.cli.RecentFolders", lambda: history)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["2", str(photos), "n", "3", "3"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert main([]) == 0
    assert "update_required" in capsys.readouterr().out
    assert history.load() == [str(photos)]
    assert list(photos.iterdir()) == [photos / "photo.heic"]


def test_history_keeps_three_unique_folders_and_handles_corruption(tmp_path):
    history = RecentFolders(tmp_path / "history.json")
    history.storage.write_text("not valid JSON", encoding="utf-8")
    assert history.load() == []
    for name in ["a", "b", "c", "d", "b"]:
        history.remember(tmp_path / name)
    assert history.load() == [str(tmp_path / name) for name in ["b", "d", "c"]]
    history.storage.write_text('{"lastUsedPaths": "incorrect type"}', encoding="utf-8")
    assert history.load() == []

# OneDrive Fixer

A Python 3.14 tool for fixing Apple media filenames uploaded by OneDrive and converting HEIC photos to JPEG. Run it as an interactive menu or use commands in scripts. The media services have no terminal dependencies and can be reused by a future GUI.

## Setup

Install Python 3.14. Package installation supplies the photo and video libraries: Pillow, pillow-heif, PyAV, and Send2Trash. Standard Windows, macOS, and Linux wheels include the native codec libraries, so no separate ExifTool or FFmpeg installation is required.

From this directory in PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\onedrive-fixer.exe
```

If you use [uv](https://docs.astral.sh/uv/), the committed lockfile provides reproducible dependencies:

```powershell
uv sync --extra dev
uv run onedrive-fixer
```

## Commands

Commands preview by default. `--apply` changes files; `--yes` confirms without prompting and is required for unattended changes.

```powershell
# Preview filename changes throughout the folder tree.
onedrive-fixer fix-names 'C:\Photos'

# Apply filename changes after reviewing the preview.
onedrive-fixer fix-names 'C:\Photos' --apply --yes

# Preview HEIC conversion, without writing media files.
onedrive-fixer convert-heic 'C:\Photos' --dry-run

# Convert and keep HEIC originals.
onedrive-fixer convert-heic 'C:\Photos' --apply --yes --keep-original

# Convert and move the originals to the Recycle Bin (legacy behavior).
onedrive-fixer convert-heic 'C:\Photos' --apply --yes

# Machine-readable preview.
onedrive-fixer fix-names 'C:\Photos' --json
```

Activate `.venv` or use the full executable path from the setup example. `python -m onedrive_fixer` is also supported.

- Name fixing scans `.heic`, `.jpg`, `.jpeg`, `.png`, `.mp4`, and `.mov`, ignoring extension case. It changes only files whose metadata `Make` is Apple and whose names start with `YYYYMMDD_HHMMSS`.
- Capture-local timestamps replace the filename prefix. Suffixes and extension case are retained; attached milliseconds receive a separator: `20241101_164622039_iOS.heic` becomes `20241101_094622_039_iOS.heic`.
- Photo metadata comes from EXIF `Make` and `DateTimeDigitized`. Video metadata comes from the MOV/MP4 container's creation time and camera make, with Apple's explicit capture offset applied when available. Files without a usable creation date are skipped.
- HEIC conversion writes `.jpeg` beside each source. Quality defaults to 100, matching the original implementation; set `--quality 1-100` to change it.
- Existing destinations and duplicate planned destinations are reported as conflicts. Files changed since scanning are rejected. Links and junctions inside the scanned tree are skipped.
- Each JPEG is staged and decoded successfully before publication and source recycling. Failed conversion preserves the original. A recycling failure preserves both files and reports their state.
- Dry runs only scan and validate plans. They do not test HEIC decoding or Recycle Bin availability.
- Interactive mode remembers the three most recent folders in `%LOCALAPPDATA%\onedrive-fixer\history.json` on Windows; it can read the old temporary-folder history on first use.

Exit codes: `0` success (including a clean preview or declined confirmation), `1` file errors/conflicts or an operational failure, `2` invalid command arguments, `130` cancellation. Skipped non-Apple files and unknown dates are normal scan results.

## Development

Install local style hooks once per checkout:

```powershell
python -m pip install pre-commit
pre-commit install
```

With uv, use `uv tool install pre-commit` instead of the pip command.

Hooks apply Ruff lint fixes and formatting using `pyproject.toml`, and check whitespace,
YAML, JSON, TOML, and file sizes. To run the same checks manually:

```powershell
pre-commit run --all-files --show-diff-on-failure
```

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
```

GitHub Actions runs `build` on Windows, Linux, and macOS with Python 3.14 and locked
dependencies, tests the installed package, builds a wheel and source distribution,
and checks the CLI entry point. The separate `maintenance` workflow runs the same
pre-commit hooks and commits passing fixes as `chore: maintenance` on same-repository
branches. Fork pull requests receive checks without automatic commits. Both workflows
run on branch pushes, pull requests to `main`, and manual dispatches. Automatic fixes
require repository Actions permissions to allow writing contents and branch rules
that permit the bot to push.

See [the design document](docs/design.md) for behavior, architecture, safety decisions, and GUI integration, and [development notes](docs/development.md) for the project layout and optional legacy-media checks.

## License

Licensed under the [MIT License](LICENSE).

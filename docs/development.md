# Development

## Environment

Use Python 3.14. The `.python-version` file also selects 3.14 for uv. Run `uv sync --extra dev` with the committed lockfile, or create a virtual environment and run `python -m pip install -e '.[dev]'`. Runtime dependencies are PyAV, Pillow, pillow-heif, and Send2Trash; development dependencies are pytest, Ruff, and build.

Metadata is read in process through Pillow and PyAV. Standard wheels supply the native libraries, so no ExifTool or FFmpeg executable installation is required. The optional `.tools/` directory is ignored and may hold workspace-local development tools. The application does not depend on that directory or the legacy TypeScript checkout.

## Checks

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build
onedrive-fixer --help
```

Install `pre-commit` with `python -m pip install pre-commit`, then run `pre-commit install`
once per checkout. Normal Git commits, including commits from VS Code, then run the
hooks in `.pre-commit-config.yaml`. After editing code, run
`pre-commit run --files <changed files>` to apply Ruff lint fixes and formatting and
check file hygiene. Use `pre-commit run --all-files --show-diff-on-failure` for a full
check. Ruff hooks use the existing `pyproject.toml` rules; keep their pinned revision
aligned with the Ruff version in `uv.lock` when upgrading tools.

GitHub Actions uses one `maintenance` workflow to apply and verify these hooks before
committing passing fixes on writable same-repository branches. Fork pull requests only
run checks. The separate `build` workflow installs locked dependencies with
`uv sync --locked --extra dev --no-editable`, runs pytest against the installed package,
builds both distribution formats with `uv build`, and checks `onedrive-fixer --help` on
Windows, Linux, and macOS with Python 3.14. Optional legacy-media tests remain skipped
unless their fixture environment variable is set.

### Executable checks

The `executable` dependency group locks PyInstaller and its packaging hooks. On
Windows, build `dist/onedrive-fixer.exe` using the [README instructions](../README.md#standalone-windows-executable).
Build with 64-bit Python for a Windows x64 executable. The build script uses the
existing module entry point, retains the terminal for interactive prompts, and
puts generated specifications and intermediate files in ignored `build/`.
PyInstaller's hooks collect Pillow plugins and PyAV's bundled FFmpeg libraries;
native HEIF dependencies are collected through binary dependency analysis.

After building, run the optional integration checks:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.tools\executable-env"
$env:ONEDRIVE_FIXER_EXE = "$PWD\dist\onedrive-fixer.exe"
uv run --locked --extra dev --group executable --no-editable python -m pytest tests/test_executable.py
Remove-Item Env:ONEDRIVE_FIXER_EXE
```

These checks copy only the executable to a temporary folder, run it outside the
checkout with Python removed from `PATH`, and verify CLI help, error exit codes,
photo/video metadata, HEIC conversion, and JPEG optimization. Generated media is
used throughout; conversion retains originals and optimization retains backups.
Interactive terminal behavior and actual Recycle Bin calls remain manual checks.
The tests skip unless `ONEDRIVE_FIXER_EXE` is set. Rebuild and rerun them whenever
application code, packaging tools, or media dependencies change.

### Documentation style

Document source modules, types, functions, and methods with concise Python docstrings. Place the opening and closing triple quotes on their own lines, including for a single sentence. Describe behavior and contracts, especially defaults, side effects, returned statuses, and error handling; aim for two or three content lines unless the behavior needs more explanation. Keep field-specific notes and implementation comments short.

Ruff checks public source docstring coverage and requires multiline summaries to start below the opening quotes. Tests are exempt from docstring checks; their names should describe the behavior being verified.

Tests use temporary folders, generated JPEG/PNG/HEIC images with EXIF, and generated MOV/MP4 containers with Apple metadata. Recycle Bin calls are replaced with recording/failing adapters, so tests do not recycle personal files. Codec tests exercise real encoding and decoding. The ordinary suite does not require the old project or external media executables.

For optional compatibility tests against the original sample photos and video:

```powershell
$env:ONEDRIVE_FIXER_LEGACY_MEDIA = 'C:\Stuff\GitHub\Learnings\web-dev-playground\projects\onedrive-fixer\test\media'
python -m pytest -m legacy
```

These tests only read the original fixtures. The image adapter writes JPEG output under pytest's temporary directory without calling the Recycle Bin adapter. Neither the legacy source nor its media contents are modified. Leave this environment variable unset to skip the optional tests.

## Adding a UI or adapter

Keep dialogs, prompts, terminal output, and preference choices inside `ui/`. Reuse the services and immutable results described in [design.md](design.md). Implement `MetadataReader`, `ImageConverter`, or `Trash` to change an external backend. The `ImageConverter` contract requires a fully decoded/verified JPEG at the supplied staging destination before returning.

Source goes under `src/onedrive_fixer/`, tests under `tests/`, and design/development material under `docs/`. Generated artifacts belong in ignored `dist/` and environments in ignored `.venv/`.

## Migration verification

Verified on Windows with CPython 3.14.8:

- 61 tests passed after replacing ExifTool with the Pillow/PyAV adapter, including the optional original-media checks. One symlink test was skipped because creating symlinks was unavailable in the test process.
- The original five-file scan produced three planned fixes, one unchanged filename, and one skipped non-Apple file, matching the TypeScript tests.
- Both original HEIC samples decoded to JPEG, with Apple EXIF retained and source content hashes unchanged.
- Generated multi-image HEIC coverage confirmed primary-image export. Filesystem tests covered collision handling, stale plans, staging cleanup, and a destination created during publication.
- Generated JPEG/PNG/HEIC metadata and MOV/MP4 containers verified creation timestamps, explicit capture offsets, duplicate tags, and missing/invalid dates. CLI and video-reading tests reject attempted subprocess launches.
- Ruff lint and formatting checks passed. Wheel and source distributions built successfully, and the suite also passed against a non-editable wheel installation in a separate Python 3.14 environment.

Recycle Bin interactions were tested with recording/failing adapters, without recycling personal files. Actual operating-system recycling and OneDrive synchronization behavior remain manual integration checks.

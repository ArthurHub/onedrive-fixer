"""
Build a self-contained Windows console executable with the locked packaging tools.
"""

import sys
from pathlib import Path

import PyInstaller.__main__


def main() -> None:
    """
    Bundle the CLI and native media libraries into dist/onedrive-fixer.exe.
    Keep generated specifications and intermediate files in the ignored build folder.
    """
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows.")
    root = Path(__file__).resolve().parents[1]
    PyInstaller.__main__.run(
        [
            "--clean",
            "--noconfirm",
            "--onefile",
            "--console",
            "--noupx",
            "--name=onedrive-fixer",
            f"--paths={root / 'src'}",
            f"--distpath={root / 'dist'}",
            f"--workpath={root / 'build' / 'pyinstaller'}",
            f"--specpath={root / 'build'}",
            str(root / "src" / "onedrive_fixer" / "__main__.py"),
        ]
    )


if __name__ == "__main__":
    main()

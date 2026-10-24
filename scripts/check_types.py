"""
Run the project's locked type checker from Git hooks, including local uv installs.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """
    Locate uv and check the complete configured source tree in its project environment.
    Fall back to the repository-local tool when uv is absent from PATH.
    """
    root = Path(__file__).resolve().parents[1]
    executable = "uv.exe" if sys.platform == "win32" else "uv"
    uv = shutil.which("uv") or root / ".tools" / "uv" / "bin" / executable
    if not Path(uv).is_file():
        print("Type checking requires uv. Install it and restart your terminal.", file=sys.stderr)
        return 1
    return subprocess.call([str(uv), "run", "--locked", "--extra", "dev", "pyright"], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())

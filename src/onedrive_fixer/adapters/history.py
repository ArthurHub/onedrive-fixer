"""
Recent-folder persistence, independent of terminal prompts.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import cast


class RecentFolders:
    """
    JSON-backed history of the five most recently selected unique folder paths.
    Read the former temporary-folder cache when the configured history is absent.
    """

    def __init__(self, storage: Path | None = None) -> None:
        """
        Use an explicit history file or the platform's user configuration directory.
        Defer filesystem writes until a folder is remembered.
        """
        config_root = Path(
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_CONFIG_HOME")
            or Path.home() / ".config"
        )
        self.storage = storage or config_root / "onedrive-fixer" / "history.json"

    def load(self) -> list[str]:
        """
        Return up to five unique paths, ordered from most to least recently used.
        Ignore malformed entries and return an empty history for unreadable data.
        """
        storage = self.storage
        if not storage.exists():
            storage = Path(tempfile.gettempdir()) / "onedrive-fixer-data.json"
        try:
            value = json.loads(storage.read_text(encoding="utf-8"))
            paths = value.get("lastUsedPaths", [])
            if not isinstance(paths, list):
                return []
            entries = cast(list[object], paths)
            return list(dict.fromkeys(item for item in entries if isinstance(item, str)))[:5]
        except OSError, ValueError, AttributeError:
            return []

    def remember(self, folder: Path) -> None:
        """
        Move the folder to the front of history and atomically replace the JSON file.
        Keep at most five unique paths; filesystem failures propagate to the caller.
        """
        paths = list(dict.fromkeys([str(folder), *self.load()]))[:5]
        self.storage.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(dir=self.storage.parent, suffix=".json")
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"lastUsedPaths": paths}, stream, indent=2)
                stream.write("\n")
            temporary.replace(self.storage)
        finally:
            temporary.unlink(missing_ok=True)

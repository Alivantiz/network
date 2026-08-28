"""Locating the external binaries the pipeline shells out to.

``shutil.which`` only searches ``PATH``. A virtualenv that is not activated is
the common case where that is not enough: running ``.venv/bin/yt2tt`` directly
leaves ``yt-dlp`` sitting next to the interpreter but outside ``PATH``, so we
look in the interpreter's own directory as a fallback and hand the resolved
path to :mod:`subprocess`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def find_tool(name: str) -> str | None:
    """Return the full path to the *name* executable, or None if it is absent."""
    found = shutil.which(name)
    if found:
        return found
    return shutil.which(name, path=str(Path(sys.executable).parent))


def require_tool(name: str, error: type[Exception], hint: str = "") -> str:
    """Like :func:`find_tool`, but raise *error* when the tool is missing."""
    path = find_tool(name)
    if path is None:
        suffix = f" — {hint}" if hint else ""
        raise error(f"{name} not found in PATH{suffix}")
    return path

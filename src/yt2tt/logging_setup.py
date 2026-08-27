"""Logging configuration."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(verbose: bool = False, log_file: Path | None = None) -> None:
    """Configure root logging once, for CLI use."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)-7s %(name)-16s %(message)s"
    datefmt = "%H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(stream)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(file_handler)

    # These are chatty at DEBUG and rarely useful.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

"""Finding the external binaries, including inside a non-activated virtualenv."""

from __future__ import annotations

import pytest

from yt2tt.errors import DownloadError
from yt2tt.tools import find_tool, require_tool

TOOL = "yt2tt-fake-tool"


@pytest.fixture
def isolated_path(monkeypatch):
    """Empty PATH, so only what a test creates can be found."""
    monkeypatch.setenv("PATH", "")


def make_executable(directory, name=TOOL):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_missing_tool_is_reported_as_none(isolated_path, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.executable", str(tmp_path / "bin" / "python"))
    assert find_tool(TOOL) is None


def test_tool_on_path_is_found(isolated_path, tmp_path, monkeypatch):
    made = make_executable(tmp_path / "somewhere")
    monkeypatch.setenv("PATH", str(tmp_path / "somewhere"))
    assert find_tool(TOOL) == str(made)


def test_tool_next_to_the_interpreter_is_found(isolated_path, tmp_path, monkeypatch):
    """`.venv/bin/yt2tt` run without activating the venv still sees `.venv/bin/yt-dlp`."""
    made = make_executable(tmp_path / "bin")
    monkeypatch.setattr("sys.executable", str(tmp_path / "bin" / "python"))
    assert find_tool(TOOL) == str(made)


def test_require_tool_raises_the_requested_error(isolated_path, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.executable", str(tmp_path / "bin" / "python"))
    with pytest.raises(DownloadError, match="not found in PATH — install it"):
        require_tool(TOOL, DownloadError, "install it")

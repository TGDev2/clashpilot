"""Pytest plugin: keep collection inside the folder of arguments located outside the project.

By default pytest cuts conftest discovery at the folder of the configuration file. For a test path
outside the project (for example in the system temp folder) it then walks every parent up to the
drive root and scans each level. That is slow and fails when entries vanish during the scan. For
such arguments the cut-off moves to their own folder, so only that folder is collected.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


def _argument_folder(raw: str) -> Path | None:
    path = Path(raw.split("::", 1)[0])
    if not path.exists():
        return None
    resolved = path.resolve()
    return resolved if resolved.is_dir() else resolved.parent


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config, args: list[str]) -> None:
    namespace: Any = early_config.known_args_namespace
    project_cut = str(
        early_config.inipath.parent if early_config.inipath else early_config.rootpath
    )
    if namespace.confcutdir not in (None, project_cut):
        return  # explicitly set on the command line
    root = early_config.rootpath.resolve()
    outside = [
        folder
        for raw in args
        if (folder := _argument_folder(raw)) is not None and not folder.is_relative_to(root)
    ]
    if outside:
        namespace.confcutdir = os.path.commonpath([str(folder) for folder in outside])

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import clashpilot_pytest


def _config(root: Path, confcutdir: str | None = None) -> Any:
    inipath = root / "pyproject.toml"
    namespace = SimpleNamespace(confcutdir=confcutdir if confcutdir is not None else str(root))
    return SimpleNamespace(inipath=inipath, rootpath=root, known_args_namespace=namespace)


def test_argument_outside_root_moves_the_cut_off_to_its_folder(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    target = outside / "test_x.py"
    target.write_text("", encoding="utf-8")
    config = _config(root)

    clashpilot_pytest.pytest_load_initial_conftests(config, [f"{target}::test_x"])

    assert Path(config.known_args_namespace.confcutdir) == outside.resolve()


def test_argument_inside_root_keeps_the_default_cut_off(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    config = _config(root)

    clashpilot_pytest.pytest_load_initial_conftests(config, [str(root / "tests")])

    assert config.known_args_namespace.confcutdir == str(root)


def test_explicit_cut_off_is_respected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    config = _config(root, confcutdir=str(tmp_path))

    clashpilot_pytest.pytest_load_initial_conftests(config, [str(outside)])

    assert config.known_args_namespace.confcutdir == str(tmp_path)


def test_plugin_is_registered_by_the_project_configuration(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.pluginmanager.has_plugin("clashpilot_pytest")

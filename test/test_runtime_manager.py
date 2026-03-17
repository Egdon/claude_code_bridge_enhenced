from __future__ import annotations

from pathlib import Path

import pytest

from runtime_manager import (
    ensure_runtime_dir_for_target,
    runtime_dir_for_target,
    runtime_instances_root,
)


def test_runtime_instances_root_uses_instances_subdir(tmp_path: Path) -> None:
    assert runtime_instances_root(tmp_path) == tmp_path / "instances"


def test_runtime_dir_for_target_nests_provider_and_instance(tmp_path: Path) -> None:
    runtime_dir = runtime_dir_for_target(tmp_path, "codex@1")

    assert runtime_dir == tmp_path / "instances" / "codex" / "1"
    assert "@" not in runtime_dir.name
    assert runtime_dir.parts[-3:] == ("instances", "codex", "1")


def test_ensure_runtime_dir_for_target_creates_nested_directory(tmp_path: Path) -> None:
    runtime_dir = ensure_runtime_dir_for_target(tmp_path, "claude@main")

    assert runtime_dir.exists()
    assert runtime_dir.is_dir()
    assert runtime_dir == tmp_path / "instances" / "claude" / "main"


def test_runtime_dir_for_target_rejects_invalid_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        runtime_dir_for_target(tmp_path, "codex")

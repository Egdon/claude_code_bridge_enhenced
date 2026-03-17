from __future__ import annotations

from pathlib import Path

from target_id import split_target, validate_target


def runtime_instances_root(base_runtime_dir: Path | str) -> Path:
    return Path(base_runtime_dir) / "instances"


def runtime_dir_for_target(base_runtime_dir: Path | str, target: str) -> Path:
    provider, instance = split_target(validate_target(target))
    return runtime_instances_root(base_runtime_dir) / provider / instance


def ensure_runtime_dir_for_target(base_runtime_dir: Path | str, target: str) -> Path:
    runtime_dir = runtime_dir_for_target(base_runtime_dir, target)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir

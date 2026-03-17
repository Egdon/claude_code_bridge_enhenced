from __future__ import annotations

import json
from pathlib import Path

import ccb_start_config


def test_read_plaintext_target_config_normalizes_targets_and_cmd(tmp_path: Path) -> None:
    config_path = tmp_path / "ccb.config"
    config_path.write_text("codex@1,codex@2,claude@main,cmd\n", encoding="utf-8")

    data = ccb_start_config._read_config(config_path)

    assert data["targets"] == ["codex@1", "codex@2", "claude@main"]
    assert data["providers"] == ["codex", "claude"]
    assert data["cmd"] is True


def test_read_json_target_config_dedupes_targets_preserving_order(tmp_path: Path) -> None:
    config_path = tmp_path / "ccb.config"
    config_path.write_text(
        '{"targets": ["codex@2", "codex@2", "codex@1", "cmd", "claude@main"]}\n',
        encoding="utf-8",
    )

    data = ccb_start_config._read_config(config_path)

    assert data["targets"] == ["codex@2", "codex@1", "claude@main"]
    assert data["providers"] == ["codex", "claude"]
    assert data["cmd"] is True


def test_read_legacy_provider_config_maps_to_main_targets(tmp_path: Path) -> None:
    config_path = tmp_path / "ccb.config"
    config_path.write_text("codex,opencode,cursor\n", encoding="utf-8")

    data = ccb_start_config._read_config(config_path)

    assert data["targets"] == ["codex@main", "opencode@main", "cursor@main"]
    assert data["providers"] == ["codex", "opencode", "cursor"]


def test_read_json_legacy_provider_config_maps_to_main_targets(tmp_path: Path) -> None:
    config_path = tmp_path / "ccb.config"
    config_path.write_text(
        json.dumps({"providers": ["codex", "codex", "cursor", "cmd"]}) + "\n",
        encoding="utf-8",
    )

    data = ccb_start_config._read_config(config_path)

    assert data["targets"] == ["codex@main", "cursor@main"]
    assert data["providers"] == ["codex", "cursor"]
    assert data["cmd"] is True


def test_ensure_default_start_config_writes_target_defaults(tmp_path: Path) -> None:
    config_path, created = ccb_start_config.ensure_default_start_config(tmp_path)

    assert created is True
    assert config_path is not None
    assert config_path.read_text(encoding="utf-8") == "codex@main,opencode@main,cursor@main\n"

from __future__ import annotations

import json
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

from session_utils import project_config_dir


def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_kill_targets", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_kill_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _write_control_plane(tmp_path: Path, payload: dict) -> Path:
    path = project_config_dir(tmp_path) / "control-plane.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_cmd_kill_target_only_kills_requested_target(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    killed: list[str] = []
    removed: list[str] = []
    daemon_shutdown: list[str] = []

    monkeypatch.setattr(ccb, "_resolve_kill_targets", lambda project_id, targets=None, provider=None: ["codex@2"])
    monkeypatch.setattr(ccb, "_kill_target", lambda work_dir, project_id, target: killed.append(target) or "codex")
    monkeypatch.setattr(ccb, "record_target_removal", lambda work_dir, target: removed.append(target) or None)
    monkeypatch.setattr(ccb, "_providers_requiring_shutdown_after_target_kill", lambda project_id, killed_targets: [])
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda provider: daemon_shutdown.append(provider))
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-1")

    rc = ccb.cmd_kill(SimpleNamespace(force=False, yes=False, targets=["codex@2"], provider=None))

    assert rc == 0
    assert killed == ["codex@2"]
    assert removed == ["codex@2"]
    assert daemon_shutdown == []


def test_cmd_kill_target_updates_control_plane_state(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    control_plane_path = _write_control_plane(
        tmp_path,
        {
            "schema_version": 1,
            "session_id": "sess-ctl",
            "runtime_root": str(tmp_path / "runtime-root"),
            "project_id": "proj-ctl",
            "terminal": "tmux",
            "active_targets": ["codex@1", "codex@2", "claude@main"],
            "target_panes": {
                "codex@1": "%11",
                "codex@2": "%12",
                "claude@main": "%21",
            },
            "parent_target": "codex@2",
            "parent_pane_id": "%12",
        },
    )

    killed: list[str] = []
    daemon_shutdown: list[str] = []

    monkeypatch.setattr(ccb, "_resolve_kill_targets", lambda project_id, targets=None, provider=None: ["codex@2"])
    monkeypatch.setattr(ccb, "_kill_target", lambda work_dir, project_id, target: killed.append(target) or "codex")
    monkeypatch.setattr(ccb, "_providers_requiring_shutdown_after_target_kill", lambda project_id, killed_targets: [])
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda provider: daemon_shutdown.append(provider))
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-ctl")

    rc = ccb.cmd_kill(SimpleNamespace(force=False, yes=False, targets=["codex@2"], provider=None))

    assert rc == 0
    assert killed == ["codex@2"]
    assert daemon_shutdown == []

    payload = json.loads(control_plane_path.read_text(encoding="utf-8"))
    assert payload["active_targets"] == ["codex@1", "claude@main"]
    assert payload["target_panes"] == {"codex@1": "%11", "claude@main": "%21"}
    assert payload["parent_target"] == "codex@1"
    assert payload["parent_pane_id"] == "%11"


def test_cmd_kill_provider_kills_all_provider_targets_and_shutdowns(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    killed: list[str] = []
    removed: list[str] = []
    daemon_shutdown: list[str] = []

    monkeypatch.setattr(ccb, "_resolve_kill_targets", lambda project_id, targets=None, provider=None: ["codex@1", "codex@2"])
    monkeypatch.setattr(ccb, "_kill_target", lambda work_dir, project_id, target: killed.append(target) or "codex")
    monkeypatch.setattr(ccb, "record_target_removal", lambda work_dir, target: removed.append(target) or None)
    monkeypatch.setattr(ccb, "_providers_requiring_shutdown_after_target_kill", lambda project_id, killed_targets: ["codex"])
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda provider: daemon_shutdown.append(provider))
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-1")

    rc = ccb.cmd_kill(SimpleNamespace(force=False, yes=False, targets=[], provider="codex"))

    assert rc == 0
    assert killed == ["codex@1", "codex@2"]
    assert removed == ["codex@1", "codex@2"]
    assert daemon_shutdown == ["codex"]


def test_cmd_kill_provider_updates_control_plane_for_each_target(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    control_plane_path = _write_control_plane(
        tmp_path,
        {
            "schema_version": 1,
            "session_id": "sess-ctl",
            "runtime_root": str(tmp_path / "runtime-root"),
            "project_id": "proj-ctl",
            "terminal": "tmux",
            "active_targets": ["codex@1", "codex@2", "claude@main"],
            "target_panes": {
                "codex@1": "%11",
                "codex@2": "%12",
                "claude@main": "%21",
            },
            "parent_target": "codex@2",
            "parent_pane_id": "%12",
        },
    )

    killed: list[str] = []
    daemon_shutdown: list[str] = []

    monkeypatch.setattr(ccb, "_resolve_kill_targets", lambda project_id, targets=None, provider=None: ["codex@1", "codex@2"])
    monkeypatch.setattr(ccb, "_kill_target", lambda work_dir, project_id, target: killed.append(target) or "codex")
    monkeypatch.setattr(ccb, "_providers_requiring_shutdown_after_target_kill", lambda project_id, killed_targets: ["codex"])
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda provider: daemon_shutdown.append(provider))
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-ctl")

    rc = ccb.cmd_kill(SimpleNamespace(force=False, yes=False, targets=[], provider="codex"))

    assert rc == 0
    assert killed == ["codex@1", "codex@2"]
    assert daemon_shutdown == ["codex"]

    payload = json.loads(control_plane_path.read_text(encoding="utf-8"))
    assert payload["active_targets"] == ["claude@main"]
    assert payload["target_panes"] == {"claude@main": "%21"}
    assert payload["parent_target"] == "claude@main"
    assert payload["parent_pane_id"] == "%21"


def test_cmd_kill_without_args_kills_all_targets(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    killed: list[str] = []

    monkeypatch.setattr(ccb, "_resolve_kill_targets", lambda project_id, targets=None, provider=None: ["codex@1", "gemini@main"])
    monkeypatch.setattr(ccb, "_kill_target", lambda work_dir, project_id, target: killed.append(target) or target.split("@", 1)[0])
    monkeypatch.setattr(ccb, "_providers_requiring_shutdown_after_target_kill", lambda project_id, killed_targets: ["codex", "gemini"])
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda _provider: None)
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-1")

    rc = ccb.cmd_kill(SimpleNamespace(force=False, yes=False, targets=[], provider=None))

    assert rc == 0
    assert killed == ["codex@1", "gemini@main"]


def test_cmd_kill_without_args_clears_control_plane_targets(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    control_plane_path = _write_control_plane(
        tmp_path,
        {
            "schema_version": 1,
            "session_id": "sess-ctl",
            "runtime_root": str(tmp_path / "runtime-root"),
            "project_id": "proj-ctl",
            "terminal": "tmux",
            "active_targets": ["codex@1", "claude@main"],
            "target_panes": {
                "codex@1": "%11",
                "claude@main": "%21",
            },
            "parent_target": "claude@main",
            "parent_pane_id": "%21",
        },
    )

    killed: list[str] = []
    daemon_shutdown: list[str] = []

    monkeypatch.setattr(ccb, "_resolve_kill_targets", lambda project_id, targets=None, provider=None: ["codex@1", "claude@main"])
    monkeypatch.setattr(ccb, "_kill_target", lambda work_dir, project_id, target: killed.append(target) or target.split("@", 1)[0])
    monkeypatch.setattr(ccb, "_providers_requiring_shutdown_after_target_kill", lambda project_id, killed_targets: ["codex", "claude"])
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda provider: daemon_shutdown.append(provider))
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-ctl")

    rc = ccb.cmd_kill(SimpleNamespace(force=False, yes=False, targets=[], provider=None))

    assert rc == 0
    assert killed == ["codex@1", "claude@main"]
    assert daemon_shutdown == ["codex", "claude"]

    payload = json.loads(control_plane_path.read_text(encoding="utf-8"))
    assert "active_targets" not in payload
    assert "target_panes" not in payload
    assert "parent_target" not in payload
    assert "parent_pane_id" not in payload


def test_cmd_kill_rejects_bare_provider_without_flag(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-1")

    rc = ccb.cmd_kill(SimpleNamespace(force=False, yes=False, targets=["codex"], provider=None))

    assert rc == 2
    assert "--provider codex" in capsys.readouterr().err

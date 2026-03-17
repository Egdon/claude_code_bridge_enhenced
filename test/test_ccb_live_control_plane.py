from __future__ import annotations

import importlib.util
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

from control_plane import load_control_plane, ping_control_plane, record_target_activation, request_control_plane_operation
from control_plane_server import start_control_plane_server
from project_id import compute_ccb_project_id


def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_live_control_plane", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_live_control_plane", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_live_control_add_and_rm_update_control_plane_state(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    launcher = ccb.AILauncher(targets=["claude@main"])
    launcher.project_root = tmp_path
    launcher.project_id = compute_ccb_project_id(tmp_path)
    launcher.terminal_type = "tmux"
    launcher.anchor_target = "claude@main"
    launcher.anchor_provider = "claude"
    launcher.anchor_pane_id = "%0"

    record_target_activation(
        tmp_path,
        session_id=launcher.session_id,
        runtime_root=launcher.runtime_dir,
        project_id=launcher.project_id,
        terminal="tmux",
        target="claude@main",
        pane_id="%0",
    )

    started: list[tuple[str, str | None, str | None]] = []

    def _fake_start_target(target: str, *, parent_pane: str | None = None, direction: str | None = None) -> str:
        started.append((target, parent_pane, direction))
        launcher._record_control_plane_target(target, pane_id="%12")
        return "%12"

    monkeypatch.setattr(launcher, "_start_target", _fake_start_target)
    monkeypatch.setattr(launcher, "_warmup_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_maybe_start_caskd", lambda: None)
    monkeypatch.setattr(launcher, "_sync_cend_registry", lambda: None)
    monkeypatch.setattr(ccb.target_cmd_utils, "ping_target", lambda work_dir, target: (False, "missing"))
    monkeypatch.setattr(ccb.target_cmd_utils, "kill_target", lambda work_dir, target: (True, f"✅ removed {target}"))

    handle = start_control_plane_server(tmp_path, token="secret-token", request_handler=launcher._handle_control_plane_request)
    launcher._control_plane_server = handle
    try:
        add_reply = request_control_plane_operation(tmp_path, op="add", target="codex@2")
        assert add_reply is not None
        assert add_reply["exit_code"] == 0
        assert add_reply["changed"] is True
        assert add_reply["active_targets"] == ["claude@main", "codex@2"]
        assert started == [("codex@2", "%0", "bottom")]

        payload = load_control_plane(tmp_path)
        assert payload is not None
        assert payload["active_targets"] == ["claude@main", "codex@2"]
        assert payload["target_panes"] == {"claude@main": "%0", "codex@2": "%12"}
        assert payload["parent_target"] == "codex@2"
        assert payload["parent_pane_id"] == "%12"
        assert payload["status"] == "running"

        rm_reply = request_control_plane_operation(tmp_path, op="rm", target="codex@2")
        assert rm_reply is not None
        assert rm_reply["exit_code"] == 0
        assert rm_reply["changed"] is True
        assert rm_reply["active_targets"] == ["claude@main"]

        payload = load_control_plane(tmp_path)
        assert payload is not None
        assert payload["active_targets"] == ["claude@main"]
        assert payload["target_panes"] == {"claude@main": "%0"}
        assert payload["parent_target"] == "claude@main"
        assert payload["parent_pane_id"] == "%0"
        assert payload["status"] == "running"
    finally:
        handle.close()


def test_run_up_starts_control_plane_server_and_cleanup_stops_it(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb, "detect_terminal", lambda: "tmux")
    monkeypatch.setattr(ccb, "_cleanup_tmpclaude_artifacts", lambda: 0)
    monkeypatch.setattr(ccb, "_cleanup_stale_runtime_dirs", lambda exclude=None: 0)
    monkeypatch.setattr(ccb, "_shrink_ccb_logs", lambda: 0)

    launcher = ccb.AILauncher(targets=["claude@main"])
    launcher.terminal_type = "tmux"

    seen: dict[str, bool] = {
        "server_running_before_anchor": False,
        "server_present_during_cleanup": False,
    }

    monkeypatch.setattr(launcher, "_require_project_config_dir", lambda: True)
    monkeypatch.setattr(launcher, "_backfill_local_claude_session_metadata", lambda: None)
    monkeypatch.setattr(launcher, "_current_pane_id", lambda: "%0")
    monkeypatch.setattr(launcher, "_set_tmux_ui_active", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_set_current_pane_label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_start_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_stop_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_sync_cend_registry", lambda: None)
    monkeypatch.setattr(launcher, "_write_local_claude_session", lambda **_kwargs: None)
    monkeypatch.setattr(ccb.atexit, "register", lambda _fn: None)
    monkeypatch.setattr(ccb.signal, "signal", lambda *_args, **_kwargs: None)

    def _fake_start_anchor(target: str) -> int:
        assert target == "claude@main"
        assert ping_control_plane(tmp_path) is True
        payload = load_control_plane(tmp_path)
        assert payload is not None
        assert payload["status"] == "running"
        seen["server_running_before_anchor"] = True
        return 0

    monkeypatch.setattr(launcher, "_start_target_in_current_pane", _fake_start_anchor)

    original_cleanup = launcher.cleanup

    def _cleanup_wrapper(**_kwargs) -> None:
        seen["server_present_during_cleanup"] = launcher._control_plane_server is not None
        original_cleanup(kill_panes=False, clear_sessions=False, remove_runtime=False, quiet=True)

    monkeypatch.setattr(launcher, "cleanup", _cleanup_wrapper)

    rc = launcher.run_up()

    assert rc == 0
    assert seen["server_running_before_anchor"] is True
    assert seen["server_present_during_cleanup"] is True

    deadline = time.time() + 2.0
    while time.time() < deadline:
        payload = load_control_plane(tmp_path)
        if isinstance(payload, dict) and payload.get("status") == "stopped":
            break
        time.sleep(0.05)

    payload = load_control_plane(tmp_path)
    assert payload is not None
    assert payload["status"] == "stopped"
    assert ping_control_plane(tmp_path) is False

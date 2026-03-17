from __future__ import annotations

import importlib.util
import sys
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace


def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_control_plane_lifecycle", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_control_plane_lifecycle", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_run_up_starts_live_control_plane_server(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb, "detect_terminal", lambda: "tmux")

    launcher = ccb.AILauncher(targets=["codex@1", "claude@main"])
    launcher.terminal_type = "tmux"

    started: dict[str, object] = {}

    class _FakeServer:
        def close(self, timeout: float = 2.0) -> None:
            started["closed"] = timeout

    fake_server = _FakeServer()

    def _fake_start_control_plane_server(work_dir, *, token, request_handler, host="127.0.0.1", port=0):
        started["work_dir"] = Path(work_dir)
        started["token"] = token
        started["request_handler"] = request_handler
        started["host"] = host
        started["port"] = port
        return fake_server

    monkeypatch.setitem(
        sys.modules,
        "control_plane_server",
        types.SimpleNamespace(start_control_plane_server=_fake_start_control_plane_server),
    )
    monkeypatch.setattr(ccb, "next_control_plane_token", lambda: "tok-1")
    monkeypatch.setattr(launcher, "_require_project_config_dir", lambda: True)
    monkeypatch.setattr(launcher, "_current_pane_id", lambda: "%0")
    monkeypatch.setattr(launcher, "_set_tmux_ui_active", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_set_current_pane_label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_start_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_record_control_plane_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_sync_cend_registry", lambda: None)
    monkeypatch.setattr(launcher, "_maybe_start_caskd", lambda: None)
    monkeypatch.setattr(launcher, "_write_local_claude_session", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "cleanup", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "_start_target", lambda target, *, parent_pane=None, direction=None: "%1")
    monkeypatch.setattr(launcher, "_start_target_in_current_pane", lambda target: 0)
    monkeypatch.setattr(ccb.atexit, "register", lambda _fn: None)
    monkeypatch.setattr(ccb.signal, "signal", lambda *_args, **_kwargs: None)

    rc = launcher.run_up()

    assert rc == 0
    assert started["work_dir"] == tmp_path.resolve()
    assert started["token"] == "tok-1"
    assert callable(started["request_handler"])
    assert launcher._control_plane_server is fake_server



def test_cleanup_closes_live_control_plane_server(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    launcher = ccb.AILauncher(targets=["claude@main"])
    launcher.terminal_type = "tmux"

    closed: list[float] = []

    class _FakeServer:
        def close(self, timeout: float = 2.0) -> None:
            closed.append(timeout)

    launcher._control_plane_server = _FakeServer()

    monkeypatch.setattr(launcher, "_stop_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_set_tmux_ui_active", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ccb, "_cleanup_tmpclaude_artifacts", lambda: None)
    monkeypatch.setattr(ccb, "_cleanup_stale_runtime_dirs", lambda **_kwargs: None)
    monkeypatch.setattr(ccb, "_shrink_ccb_logs", lambda: 0)
    monkeypatch.setattr(ccb, "state_file_path", lambda _name: tmp_path / "askd.json")
    monkeypatch.setattr(ccb, "shutdown_daemon", lambda *_args, **_kwargs: True)

    launcher.cleanup(kill_panes=False, clear_sessions=False, remove_runtime=False, quiet=True)

    assert closed == [2.0]
    assert launcher._control_plane_server is None

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import time
from types import SimpleNamespace

from control_plane import load_control_plane, ping_control_plane


def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_targets", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_parse_targets_with_cmd_accepts_instances_and_cmd() -> None:
    ccb = _load_ccb_module()

    targets, cmd_enabled = ccb._parse_targets_with_cmd(["codex@1", "codex@2", "claude@main", "cmd"])

    assert targets == ["codex@1", "codex@2", "claude@main"]
    assert cmd_enabled is True


def test_parse_targets_with_cmd_rejects_bare_provider(capsys) -> None:
    ccb = _load_ccb_module()

    targets, cmd_enabled = ccb._parse_targets_with_cmd(["codex", "claude@main"])

    assert targets == []
    assert cmd_enabled is False
    captured = capsys.readouterr()
    assert "invalid target" in captured.err.lower() or "invalid target(s)" in captured.err.lower()


def test_cmd_start_reads_targets_from_config_when_args_empty(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)

    class _FakeLock:
        lock_file = tmp_path / ".ccb" / "fake.lock"

        def __init__(self, *_args, **_kwargs):
            return None

        def try_acquire(self) -> bool:
            return True

        def release(self) -> None:
            return None

    captured: dict[str, object] = {}

    class _FakeLauncher:
        def __init__(self, *, targets, resume, auto, cmd_config):
            captured["targets"] = list(targets)
            captured["resume"] = resume
            captured["auto"] = auto
            captured["cmd_config"] = cmd_config

        def run_up(self) -> int:
            return 0

    monkeypatch.setattr(ccb, "detect_terminal", lambda: "tmux")
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb, "ProviderLock", _FakeLock)
    monkeypatch.setattr(
        ccb,
        "load_start_config",
        lambda _work_dir: SimpleNamespace(data={"targets": ["codex@1", "claude@main"], "flags": {}}, path=tmp_path / ".ccb" / "ccb.config"),
    )
    monkeypatch.setattr(ccb, "AILauncher", _FakeLauncher)
    monkeypatch.setattr(ccb.atexit, "register", lambda _fn: None)

    rc = ccb.cmd_start(SimpleNamespace(providers=[], resume=False, auto=False))

    assert rc == 0
    assert captured["targets"] == ["codex@1", "claude@main"]


def test_provider_pane_id_prefers_main_or_unique_target_without_bare_key(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    launcher = ccb.AILauncher(targets=["codex@1", "codex@main", "claude@main"])
    launcher.terminal_type = "tmux"
    launcher.tmux_panes["codex@1"] = "%11"
    launcher.tmux_panes["codex@main"] = "%10"
    launcher.tmux_panes["claude@main"] = "%20"

    assert launcher._provider_pane_id("codex") == "%10"
    assert launcher._provider_pane_id("claude") == "%20"



def test_claude_env_overrides_use_unique_target_runtime_when_main_absent(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    launcher = ccb.AILauncher(targets=["codex@2", "claude@main"])
    launcher.terminal_type = "tmux"
    launcher.tmux_panes["codex@2"] = "%12"

    env = launcher._claude_env_overrides()

    runtime = Path(launcher.runtime_dir) / "instances" / "codex" / "2"
    assert env["CODEX_RUNTIME_DIR"] == str(runtime)
    assert env["CODEX_INPUT_FIFO"] == str(runtime / "input.fifo")
    assert env["CODEX_OUTPUT_FIFO"] == str(runtime / "output.fifo")
    assert env["CODEX_TMUX_SESSION"] == "%12"



def test_claude_env_overrides_skip_ambiguous_provider_without_main(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    launcher = ccb.AILauncher(targets=["codex@1", "codex@2", "claude@main"])
    launcher.terminal_type = "tmux"
    launcher.tmux_panes["codex@1"] = "%11"
    launcher.tmux_panes["codex@2"] = "%12"

    env = launcher._claude_env_overrides()

    assert "CODEX_RUNTIME_DIR" not in env
    assert "CODEX_INPUT_FIFO" not in env
    assert "CODEX_OUTPUT_FIFO" not in env
    assert "CODEX_TMUX_SESSION" not in env



def test_run_up_starts_and_cleanup_stops_live_control_plane(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb, "detect_terminal", lambda: "tmux")
    monkeypatch.setattr(ccb.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ccb, "_cleanup_tmpclaude_artifacts", lambda: 0)
    monkeypatch.setattr(ccb, "_cleanup_stale_runtime_dirs", lambda exclude=None: 0)
    monkeypatch.setattr(ccb, "_shrink_ccb_logs", lambda: 0)
    monkeypatch.setattr(ccb, "state_file_path", lambda _name: tmp_path / ".ccb" / "askd.json")
    monkeypatch.setattr(ccb, "shutdown_daemon", lambda *_args, **_kwargs: True)

    killed: list[str] = []

    class _FakeTmuxBackend:
        def kill_pane(self, pane_id: str) -> None:
            killed.append(pane_id)

    launcher = ccb.AILauncher(targets=["codex@1", "claude@main"])
    launcher.terminal_type = "tmux"

    monkeypatch.setattr(ccb, "TmuxBackend", _FakeTmuxBackend)
    monkeypatch.setattr(launcher, "_require_project_config_dir", lambda: True)
    monkeypatch.setattr(launcher, "_current_pane_id", lambda: "%0")
    monkeypatch.setattr(launcher, "_set_tmux_ui_active", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_set_current_pane_label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_start_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_stop_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_sync_cend_registry", lambda: None)
    monkeypatch.setattr(launcher, "_maybe_start_caskd", lambda: None)
    monkeypatch.setattr(launcher, "_write_local_claude_session", lambda **_kwargs: None)
    monkeypatch.setattr(ccb.atexit, "register", lambda _fn: None)
    monkeypatch.setattr(ccb.signal, "signal", lambda *_args, **_kwargs: None)

    started: list[tuple[str, str | None, str | None]] = []
    live_handle: dict[str, object] = {}

    def _fake_start_target(target, *, parent_pane=None, direction=None):
        started.append((target, parent_pane, direction))
        pane_id = f"%{len(started)}"
        launcher._record_pane_for_target(target, pane_id, terminal="tmux")
        return pane_id

    def _fake_start_anchor(target: str) -> int:
        launcher._record_pane_for_target(target, "%0", terminal="tmux")
        handle = launcher._control_plane_server
        assert handle is not None
        live_handle["handle"] = handle
        payload = load_control_plane(tmp_path)
        assert payload is not None
        assert payload["status"] == "running"
        assert payload["host"] == handle.host
        assert payload["port"] == handle.port
        assert payload["token"] == handle.token
        assert ping_control_plane(tmp_path, timeout_s=0.5) is True
        return 0

    monkeypatch.setattr(launcher, "_start_target", _fake_start_target)
    monkeypatch.setattr(launcher, "_start_target_in_current_pane", _fake_start_anchor)

    rc = launcher.run_up()

    assert rc == 0
    assert started == [("codex@1", "%0", "right")]
    handle = live_handle["handle"]
    assert launcher._control_plane_server is None

    deadline = time.time() + 2.0
    while time.time() < deadline:
        payload = load_control_plane(tmp_path)
        if isinstance(payload, dict) and payload.get("status") == "stopped":
            break
        time.sleep(0.05)

    payload = load_control_plane(tmp_path)
    assert payload is not None
    assert payload["status"] == "stopped"
    assert ping_control_plane(tmp_path, timeout_s=0.2) is False
    assert not handle.thread.is_alive()
    assert killed == ["%1", "%0"]



def test_run_up_uses_last_target_as_anchor_and_spawns_other_targets(
    monkeypatch, tmp_path: Path
) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb, "detect_terminal", lambda: "tmux")

    launcher = ccb.AILauncher(targets=["codex@1", "codex@2", "claude@main"])
    launcher.terminal_type = "tmux"

    started: list[tuple[str, str | None, str | None]] = []

    monkeypatch.setattr(launcher, "_require_project_config_dir", lambda: True)
    monkeypatch.setattr(launcher, "_current_pane_id", lambda: "%0")
    monkeypatch.setattr(launcher, "_set_tmux_ui_active", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_set_current_pane_label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_start_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_sync_cend_registry", lambda: None)
    monkeypatch.setattr(launcher, "_maybe_start_caskd", lambda: None)
    monkeypatch.setattr(launcher, "_write_local_claude_session", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "cleanup", lambda **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_start_target",
        lambda target, *, parent_pane=None, direction=None: started.append((target, parent_pane, direction)) or f"%{len(started)}",
    )
    monkeypatch.setattr(launcher, "_start_target_in_current_pane", lambda target: 0)
    monkeypatch.setattr(ccb.atexit, "register", lambda _fn: None)
    monkeypatch.setattr(ccb.signal, "signal", lambda *_args, **_kwargs: None)

    rc = launcher.run_up()

    assert rc == 0
    assert launcher.anchor_target == "claude@main"
    assert launcher.anchor_provider == "claude"
    assert started == [
        ("codex@2", "%0", "right"),
        ("codex@1", "%1", "bottom"),
    ]


def test_run_up_starts_live_control_plane_before_anchor_start(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb, "detect_terminal", lambda: "tmux")

    launcher = ccb.AILauncher(targets=["codex@1", "claude@main"])
    launcher.terminal_type = "tmux"

    events: list[str] = []

    monkeypatch.setattr(launcher, "_require_project_config_dir", lambda: True)
    monkeypatch.setattr(launcher, "_current_pane_id", lambda: "%0")
    monkeypatch.setattr(launcher, "_set_tmux_ui_active", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_set_current_pane_label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_start_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_maybe_start_caskd", lambda: None)
    monkeypatch.setattr(launcher, "_warmup_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_write_local_claude_session", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "cleanup", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "_record_control_plane_target", lambda target, pane_id=None: events.append(f"record:{target}:{pane_id}"))
    monkeypatch.setattr(launcher, "_start_control_plane_server", lambda: events.append("start-control-plane") or True)
    monkeypatch.setattr(launcher, "_sync_cend_registry", lambda: events.append("sync-registry"))
    monkeypatch.setattr(
        launcher,
        "_start_target",
        lambda target, *, parent_pane=None, direction=None: events.append(f"spawn:{target}:{parent_pane}:{direction}") or "%1",
    )

    def _start_anchor(target: str) -> int:
        events.append(f"anchor:{target}")
        assert "start-control-plane" in events
        assert events.index("start-control-plane") < events.index(f"anchor:{target}")
        return 0

    monkeypatch.setattr(launcher, "_start_target_in_current_pane", _start_anchor)
    monkeypatch.setattr(ccb.atexit, "register", lambda _fn: None)
    monkeypatch.setattr(ccb.signal, "signal", lambda *_args, **_kwargs: None)

    rc = launcher.run_up()

    assert rc == 0
    assert events == [
        "spawn:codex@1:%0:right",
        "record:claude@main:%0",
        "start-control-plane",
        "sync-registry",
        "anchor:claude@main",
    ]


def test_cleanup_closes_live_control_plane_server(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    launcher = ccb.AILauncher(targets=["claude@main"])
    launcher.terminal_type = "tmux"

    closed: list[str] = []

    class _FakeControlPlaneServer:
        def close(self) -> None:
            closed.append("closed")

    launcher._control_plane_server = _FakeControlPlaneServer()

    monkeypatch.setattr(launcher, "_stop_daemon_watchdog", lambda: None)
    monkeypatch.setattr(launcher, "_set_tmux_ui_active", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ccb, "_cleanup_tmpclaude_artifacts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ccb, "_cleanup_stale_runtime_dirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ccb, "_shrink_ccb_logs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ccb, "state_file_path", lambda *_args, **_kwargs: None)

    launcher.cleanup(kill_panes=False, clear_sessions=False, remove_runtime=False, quiet=True)

    assert closed == ["closed"]
    assert launcher._control_plane_server is None


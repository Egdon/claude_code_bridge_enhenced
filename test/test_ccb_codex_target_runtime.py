from __future__ import annotations

import importlib.util
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path

from session_store import load_target_session


def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_codex_targets", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_codex_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_start_target_codex_tmux_uses_target_runtime_and_registry(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ccb.os, "mkfifo", lambda p, _mode=0o600: Path(p).write_text("", encoding="utf-8"))

    title_calls: list[tuple[str, str]] = []
    registry_calls: list[dict] = []

    class _FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = "right", percent: int = 50, parent_pane: str | None = None) -> str:
            return "%11"

        def respawn_pane(self, pane_id: str, *, cmd: str, cwd: str | None = None, stderr_log_path: str | None = None, remain_on_exit: bool = True) -> None:
            return None

        def set_pane_title(self, pane_id: str, title: str) -> None:
            title_calls.append((pane_id, title))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            return None

        def get_current_pane_id(self) -> str:
            return "%0"

        def pane_exists(self, pane_id: str) -> bool:
            return True

    def _fake_run(argv, *args, **kwargs):
        if argv[:3] == ["tmux", "display-message", "-p"] and "#{pane_pid}" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="12345\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = 999

    monkeypatch.setattr(ccb, "TmuxBackend", _FakeTmuxBackend)
    monkeypatch.setattr(ccb.subprocess, "run", _fake_run)
    monkeypatch.setattr(ccb.subprocess, "Popen", lambda *a, **k: _FakePopen(*a, **k))
    monkeypatch.setattr(ccb, "upsert_registry", lambda record: registry_calls.append(record) or True)

    launcher = ccb.AILauncher(targets=["codex@2"])
    launcher.terminal_type = "tmux"

    pane_id = launcher._start_target("codex@2", parent_pane="%0", direction="right")

    runtime = Path(launcher.runtime_dir) / "instances" / "codex" / "2"
    assert pane_id == "%11"
    assert runtime.exists()
    assert launcher.tmux_panes["codex@2"] == "%11"
    assert "codex" not in launcher.tmux_panes
    assert title_calls == [("%11", "CCB-Codex@2")]

    target_session = load_target_session(tmp_path, "codex@2")
    assert target_session is not None
    assert target_session["target"] == "codex@2"
    assert target_session["runtime_dir"] == str(runtime)
    assert (tmp_path / ".ccb" / ".codex-session").exists()

    assert registry_calls
    assert registry_calls[-1]["instances"]["codex@2"]["pane_title_marker"] == "CCB-Codex@2"


def test_start_target_codex_wezterm_uses_target_key_without_bare_provider(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ccb.tempfile, "gettempdir", lambda: str(tmp_path))

    class _FakeWeztermBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = "right", percent: int = 50, parent_pane: str | None = None) -> str:
            return "pane-2"

    launcher = ccb.AILauncher(targets=["codex@2"])
    launcher.terminal_type = "wezterm"
    monkeypatch.setattr(ccb, "WeztermBackend", _FakeWeztermBackend)
    monkeypatch.setattr(launcher, "_build_codex_start_cmd", lambda: "codex")

    pane_id = launcher._start_target("codex@2", parent_pane="pane-1", direction="right")

    assert pane_id == "pane-2"
    assert launcher.wezterm_panes["codex@2"] == "pane-2"
    assert "codex" not in launcher.wezterm_panes



def test_start_target_in_current_pane_routes_codex_target(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)

    launcher = ccb.AILauncher(targets=["codex@2"])
    called: dict[str, str] = {}

    def _fake_start(target: str = "codex@main") -> int:
        called["target"] = target
        return 0

    monkeypatch.setattr(launcher, "_start_codex_current_pane", _fake_start)

    rc = launcher._start_target_in_current_pane("codex@2")

    assert rc == 0
    assert called == {"target": "codex@2"}

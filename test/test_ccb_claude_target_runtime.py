from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

from session_store import load_target_session


def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_claude_targets", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_claude_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_start_target_claude_tmux_uses_target_runtime_and_registry(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMUX_PANE", "%0")
    monkeypatch.setattr(ccb.tempfile, "gettempdir", lambda: str(tmp_path))

    title_calls: list[tuple[str, str]] = []
    registry_calls: list[dict] = []

    class _FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = "right", percent: int = 50, parent_pane: str | None = None) -> str:
            return "%31"

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

    monkeypatch.setattr(ccb, "TmuxBackend", _FakeTmuxBackend)
    monkeypatch.setattr(ccb, "upsert_registry", lambda record: registry_calls.append(record) or True)

    launcher = ccb.AILauncher(targets=["claude@2"])
    launcher.terminal_type = "tmux"
    monkeypatch.setattr(launcher, "_claude_start_plan", lambda: (["claude"], str(tmp_path), False))
    monkeypatch.setattr(launcher, "_read_local_claude_session_id", lambda: None)

    pane_id = launcher._start_target("claude@2", parent_pane="%0", direction="right")

    runtime = Path(launcher.runtime_dir) / "instances" / "claude" / "2"
    assert pane_id == "%31"
    assert runtime.exists()
    assert launcher.tmux_panes["claude@2"] == "%31"
    assert "claude" not in launcher.tmux_panes
    assert title_calls == [("%31", "CCB-Claude@2")]

    target_session = load_target_session(tmp_path, "claude@2")
    assert target_session is not None
    assert target_session["target"] == "claude@2"
    assert target_session["runtime_dir"] == str(runtime)

    assert registry_calls
    assert registry_calls[-1]["instances"]["claude@2"]["pane_title_marker"] == "CCB-Claude@2"


def test_start_claude_current_pane_target_uses_target_runtime_and_registry(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)

    registry_calls: list[dict] = []
    title_calls: list[tuple[str, str]] = []
    run_calls: list[tuple[list[str], str]] = []

    class _FakeTmuxBackend:
        def set_pane_title(self, pane_id: str, title: str) -> None:
            title_calls.append((pane_id, title))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            return None

    def _fake_run(cmd: list[str], env: dict, cwd: str):
        run_calls.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ccb, "TmuxBackend", _FakeTmuxBackend)
    monkeypatch.setattr(ccb, "upsert_registry", lambda record: registry_calls.append(record) or True)
    monkeypatch.setattr(ccb.subprocess, "run", _fake_run)

    launcher = ccb.AILauncher(targets=["claude@2"])
    launcher.terminal_type = "tmux"
    monkeypatch.setattr(launcher, "_claude_start_plan", lambda: (["claude", "--continue"], str(tmp_path), False))
    monkeypatch.setattr(launcher, "_read_local_claude_session_id", lambda: None)
    monkeypatch.setattr(launcher, "_current_pane_id", lambda: "%7")

    rc = launcher._start_claude_current_pane("claude@2")

    runtime = Path(launcher.runtime_dir) / "instances" / "claude" / "2"
    assert rc == 0
    assert runtime.exists()
    assert launcher.tmux_panes["claude@2"] == "%7"
    assert "claude" not in launcher.tmux_panes
    assert title_calls == [("%7", "CCB-Claude@2")]
    assert run_calls == [(["claude", "--continue"], str(tmp_path))]

    target_session = load_target_session(tmp_path, "claude@2")
    assert target_session is not None
    assert target_session["target"] == "claude@2"
    assert target_session["runtime_dir"] == str(runtime)

    assert registry_calls
    assert registry_calls[-1]["instances"]["claude@2"]["pane_id"] == "%7"

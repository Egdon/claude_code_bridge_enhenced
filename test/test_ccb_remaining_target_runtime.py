from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from session_store import load_target_session



def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_remaining_targets", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_remaining_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.parametrize(
    ("target", "pane_title", "runtime_parts"),
    [
        ("droid@2", "CCB-Droid@2", ("instances", "droid", "2")),
        ("cursor@2", "CCB-Cursor@2", ("instances", "cursor", "2")),
        ("claude@2", "CCB-Claude@2", ("instances", "claude", "2")),
    ],
)
def test_start_target_remaining_tmux_uses_target_runtime_and_registry(
    monkeypatch, tmp_path: Path, target: str, pane_title: str, runtime_parts: tuple[str, str, str]
) -> None:
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

    launcher = ccb.AILauncher(targets=[target])
    launcher.terminal_type = "tmux"
    monkeypatch.setattr(launcher, "_maybe_start_provider_daemon", lambda _provider: None)
    monkeypatch.setattr(launcher, "_get_latest_cursor_session_info", lambda: (None, None), raising=False)
    if target.startswith("claude"):
        monkeypatch.setattr(launcher, "_claude_start_plan", lambda: (["claude"], str(tmp_path), False))

    pane_id = launcher._start_target(target, parent_pane="%0", direction="right")

    runtime = Path(launcher.runtime_dir).joinpath(*runtime_parts)
    assert pane_id == "%31"
    assert runtime.exists()
    assert launcher.tmux_panes[target] == "%31"
    assert target.split("@", 1)[0] not in launcher.tmux_panes
    assert title_calls == [("%31", pane_title)]

    target_session = load_target_session(tmp_path, target)
    assert target_session is not None
    assert target_session["target"] == target
    assert target_session["runtime_dir"] == str(runtime)

    assert registry_calls
    assert registry_calls[-1]["instances"][target]["pane_title_marker"] == pane_title

    if target.startswith("claude"):
        local_session = json.loads((tmp_path / ".ccb" / ".claude-session").read_text(encoding="utf-8"))
        assert local_session["target"] == target
        assert local_session["runtime_dir"] == str(runtime)


@pytest.mark.parametrize("target", ["droid@2", "cursor@2", "claude@2"])
def test_start_target_in_current_pane_routes_remaining_target(monkeypatch, tmp_path: Path, target: str) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ccb").mkdir(parents=True, exist_ok=True)

    launcher = ccb.AILauncher(targets=[target])
    called: dict[str, str] = {}

    if target.startswith("droid"):
        def _fake_droid(value: str = "droid@main") -> int:
            called["target"] = value
            return 0
        monkeypatch.setattr(launcher, "_start_droid_current_pane", _fake_droid)
    elif target.startswith("cursor"):
        def _fake_cursor(value: str = "cursor@main") -> int:
            called["target"] = value
            return 0
        monkeypatch.setattr(launcher, "_start_cursor_current_pane", _fake_cursor)
    else:
        def _fake_claude(value: str = "claude@main") -> int:
            called["target"] = value
            return 0
        monkeypatch.setattr(launcher, "_start_claude_current_pane", _fake_claude)

    rc = launcher._start_target_in_current_pane(target)

    assert rc == 0
    assert called == {"target": target}

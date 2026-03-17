from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import completion_hook
from session_store import write_target_session
from session_utils import project_config_dir


def _load_bin_module(name: str, path: Path) -> object:
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_completion_hook_bin() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    return _load_bin_module("ccb_completion_hook_targets", repo_root / "bin" / "ccb-completion-hook")


def test_notify_completion_exports_target_env(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_run(cmd, input=None, capture_output=None, timeout=None, env=None):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["env"] = env
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setenv("CCB_COMPLETION_HOOK_ENABLED", "1")
    monkeypatch.setattr(completion_hook.subprocess, "run", _fake_run)

    completion_hook.notify_completion(
        provider="codex",
        output_file=None,
        reply="done",
        req_id="req-1",
        done_seen=True,
        caller="claude",
        target="codex@2",
        caller_target="claude@main",
        work_dir=str(tmp_path),
    )

    env = captured["env"]
    assert env["CCB_CALLER"] == "claude"
    assert env["CCB_TARGET"] == "codex@2"
    assert env["CCB_PROVIDER"] == "codex"
    assert env["CCB_INSTANCE"] == "2"
    assert env["CCB_CALLER_TARGET"] == "claude@main"
    assert env["CCB_WORK_DIR"] == str(tmp_path)


def test_completion_hook_bin_prefers_caller_target_session(monkeypatch, tmp_path: Path) -> None:
    hook_bin = _load_completion_hook_bin()
    write_target_session(
        tmp_path,
        "claude@main",
        {
            "target": "claude@main",
            "work_dir": str(tmp_path),
            "terminal": "tmux",
            "pane_id": "%11",
            "active": True,
        },
    )

    sent: dict[str, object] = {}

    def _fake_send_via_terminal(pane_id: str, message: str, terminal: str, session_data: dict) -> bool:
        sent["pane_id"] = pane_id
        sent["message"] = message
        sent["terminal"] = terminal
        sent["session_data"] = session_data
        return True

    monkeypatch.setattr(hook_bin, "send_via_terminal", _fake_send_via_terminal)
    monkeypatch.setattr(hook_bin.sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("CCB_CALLER", "claude")
    monkeypatch.setenv("CCB_CALLER_TARGET", "claude@main")
    monkeypatch.setenv("CCB_TARGET", "codex@2")
    monkeypatch.setenv("CCB_WORK_DIR", str(tmp_path))
    monkeypatch.setattr(
        hook_bin.sys,
        "argv",
        ["ccb-completion-hook", "--provider", "codex", "--req-id", "req-1", "--reply", "done"],
    )

    rc = hook_bin.main()

    assert rc == 0
    assert sent["pane_id"] == "%11"
    assert sent["terminal"] == "tmux"
    assert sent["session_data"]["target"] == "claude@main"
    assert "Provider: Codex@2" in str(sent["message"])


def test_completion_hook_bin_does_not_fallback_to_wrong_legacy_instance(monkeypatch, tmp_path: Path) -> None:
    hook_bin = _load_completion_hook_bin()
    cfg_dir = project_config_dir(tmp_path)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / ".claude-session").write_text(
        '{"work_dir": "%s", "terminal": "tmux", "pane_id": "%%19", "target": "claude@main"}' % str(tmp_path),
        encoding="utf-8",
    )

    called: dict[str, object] = {"terminal": False, "fallback": False}

    monkeypatch.setattr(
        hook_bin,
        "send_via_terminal",
        lambda pane_id, message, terminal, session_data: called.__setitem__("terminal", True) or True,
    )
    monkeypatch.setattr(hook_bin, "find_ask_command", lambda: None)
    monkeypatch.setattr(hook_bin.sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("CCB_CALLER", "claude")
    monkeypatch.setenv("CCB_CALLER_TARGET", "claude@2")
    monkeypatch.setenv("CCB_TARGET", "codex@2")
    monkeypatch.setenv("CCB_WORK_DIR", str(tmp_path))
    monkeypatch.setattr(
        hook_bin.sys,
        "argv",
        ["ccb-completion-hook", "--provider", "codex", "--req-id", "req-2", "--reply", "done"],
    )

    rc = hook_bin.main()

    assert rc == 0
    assert called["terminal"] is False


def test_completion_hook_bin_keeps_cursor_ide_short_circuit(monkeypatch, tmp_path: Path) -> None:
    hook_bin = _load_completion_hook_bin()
    monkeypatch.setattr(
        hook_bin,
        "send_via_terminal",
        lambda pane_id, message, terminal, session_data: (_ for _ in ()).throw(AssertionError("should not notify terminal")),
    )
    monkeypatch.setenv("CCB_CALLER", "cursor-ide")
    monkeypatch.setenv("CCB_CALLER_TARGET", "cursor@main")
    monkeypatch.setenv("CCB_TARGET", "codex@2")
    monkeypatch.setenv("CCB_WORK_DIR", str(tmp_path))
    monkeypatch.setattr(hook_bin.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        hook_bin.sys,
        "argv",
        ["ccb-completion-hook", "--provider", "codex", "--req-id", "req-3", "--reply", "done"],
    )

    assert hook_bin.main() == 0

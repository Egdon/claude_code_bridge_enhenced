from __future__ import annotations

import importlib.util
import io
import os
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = REPO_ROOT / "bin"


def _load_bin_module(name: str) -> object:
    path = BIN_DIR / name
    loader = SourceFileLoader(f"test_{name}", str(path))
    spec = importlib.util.spec_from_loader(f"test_{name}", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.parametrize(
    ("module_name", "provider", "target"),
    [
        ("gask", "gemini", "gemini@2"),
        ("oask", "opencode", "opencode@2"),
        ("dask", "droid", "droid@2"),
        ("uask", "cursor", "cursor@2"),
        ("lask", "claude", "claude@2"),
    ],
)
def test_wrapper_forwards_target_to_registry_and_daemon(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    provider: str,
    target: str,
) -> None:
    mod = _load_bin_module(module_name)
    monkeypatch.chdir(tmp_path)

    captured: dict[str, object] = {}

    def _fake_resolve_work_dir_with_registry(spec, **kwargs):
        captured["resolve_kwargs"] = kwargs
        return tmp_path, None

    def _fake_try_daemon_request(spec, work_dir, message, timeout, quiet, state_file, output_path=None, target=None, caller_target=None):
        captured["request"] = {
            "work_dir": work_dir,
            "message": message,
            "target": target,
            "caller_target": caller_target,
        }
        return ("ok", 0)

    monkeypatch.setattr(mod, "resolve_work_dir_with_registry", _fake_resolve_work_dir_with_registry)
    monkeypatch.setattr(mod, "try_daemon_request", _fake_try_daemon_request)
    monkeypatch.setattr(mod, "maybe_start_daemon", lambda *_args, **_kwargs: False)

    rc = mod.main([module_name, "--target", target, "hello", provider])

    assert rc == 0
    assert captured["resolve_kwargs"]["provider"] == provider
    assert captured["resolve_kwargs"]["target"] == target
    assert captured["request"]["target"] == target



def test_ask_notify_with_explicit_target_forwards_target_to_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask = _load_bin_module("ask")

    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(ask.subprocess, "run", _fake_run)
    monkeypatch.setattr(ask, "_require_caller", lambda: "claude")
    monkeypatch.setattr(ask, "_use_unified_daemon", lambda: False)

    rc = ask.main(["ask", "claude@main", "--notify", "Task completed"])

    assert rc == 0
    assert captured["cmd"][:3] == ["lask", "--sync", "--target"]
    assert captured["cmd"][3] == "claude@main"
    assert captured["kwargs"]["input"] == "Task completed"



def test_ask_notify_with_provider_uses_caller_target_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask = _load_bin_module("ask")

    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(ask.subprocess, "run", _fake_run)
    monkeypatch.setattr(ask, "_require_caller", lambda: "claude")
    monkeypatch.setattr(ask, "_use_unified_daemon", lambda: False)
    monkeypatch.setenv("CCB_CALLER_TARGET", "claude@main")

    rc = ask.main(["ask", "claude", "--notify", "Task completed"])

    assert rc == 0
    assert captured["cmd"][:3] == ["lask", "--sync", "--target"]
    assert captured["cmd"][3] == "claude@main"



def test_ask_provider_target_foreground_routes_target_to_unified_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask = _load_bin_module("ask")

    captured: dict[str, object] = {}

    def _fake_send(provider, message, timeout, no_wrap, caller, target=None):
        captured.update(
            {
                "provider": provider,
                "message": message,
                "timeout": timeout,
                "no_wrap": no_wrap,
                "caller": caller,
                "target": target,
            }
        )
        return 0

    monkeypatch.setattr(ask, "_send_via_unified_daemon", _fake_send)
    monkeypatch.setattr(ask, "_require_caller", lambda: "claude")
    monkeypatch.setattr(ask, "_use_unified_daemon", lambda: True)

    rc = ask.main(["ask", "codex@2", "--foreground", "hello target"])

    assert rc == 0
    assert captured == {
        "provider": "codex",
        "message": "hello target",
        "timeout": 3600.0,
        "no_wrap": False,
        "caller": "claude",
        "target": "codex@2",
    }



def test_ask_provider_without_target_uses_ccb_target_in_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask = _load_bin_module("ask")

    captured: dict[str, object] = {}

    def _fake_send(provider, message, timeout, no_wrap, caller, target=None):
        captured.update({"provider": provider, "target": target})
        return 0

    monkeypatch.setattr(ask, "_send_via_unified_daemon", _fake_send)
    monkeypatch.setattr(ask, "_require_caller", lambda: "claude")
    monkeypatch.setattr(ask, "_use_unified_daemon", lambda: True)
    monkeypatch.setenv("CCB_TARGET", "codex@3")

    rc = ask.main(["ask", "codex", "--foreground", "hello env target"])

    assert rc == 0
    assert captured == {"provider": "codex", "target": "codex@3"}



def test_ask_provider_without_matching_env_target_does_not_force_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask = _load_bin_module("ask")

    captured: dict[str, object] = {}

    def _fake_send(provider, message, timeout, no_wrap, caller, target=None):
        captured.update({"provider": provider, "target": target})
        return 0

    monkeypatch.setattr(ask, "_send_via_unified_daemon", _fake_send)
    monkeypatch.setattr(ask, "_require_caller", lambda: "claude")
    monkeypatch.setattr(ask, "_use_unified_daemon", lambda: True)
    monkeypatch.setenv("CCB_TARGET", "claude@main")

    rc = ask.main(["ask", "codex", "--foreground", "hello no match"])

    assert rc == 0
    assert captured == {"provider": "codex", "target": None}



def test_ask_rejects_invalid_provider_target(monkeypatch: pytest.MonkeyPatch) -> None:
    ask = _load_bin_module("ask")

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        rc = ask.main(["ask", "badtarget", "hello"])

    assert rc == ask.EXIT_ERROR
    assert "Unknown provider" in stderr.getvalue()

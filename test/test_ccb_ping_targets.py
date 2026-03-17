from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_ping_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "bin" / "ccb-ping"
    loader = SourceFileLoader("ccb_ping_targets", str(script_path))
    spec = importlib.util.spec_from_loader("ccb_ping_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_ccb_ping_target_scope(monkeypatch, tmp_path: Path, capsys) -> None:
    ping = _load_ping_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ping, "_ping_targets", lambda targets, work_dir, autostart=False: ([("codex@2", True, "%2")], []))

    rc = ping.main(["ccb-ping", "codex@2"])

    assert rc == 0
    assert "codex@2" in capsys.readouterr().out


def test_ccb_ping_provider_scope(monkeypatch, tmp_path: Path, capsys) -> None:
    ping = _load_ping_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ping, "_resolve_scope_targets", lambda work_dir, target=None, provider=None: ["codex@1", "codex@2"])
    monkeypatch.setattr(ping, "_ping_targets", lambda targets, work_dir, autostart=False: ([(target, True, f"%{index}") for index, target in enumerate(targets, start=1)], []))

    rc = ping.main(["ccb-ping", "--provider", "codex"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "codex@1" in out and "codex@2" in out


def test_ccb_ping_all_scope(monkeypatch, tmp_path: Path, capsys) -> None:
    ping = _load_ping_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ping, "_resolve_scope_targets", lambda work_dir, target=None, provider=None: ["codex@1", "gemini@main"])
    monkeypatch.setattr(ping, "_ping_targets", lambda targets, work_dir, autostart=False: ([(target, True, f"%{index}") for index, target in enumerate(targets, start=1)], []))

    rc = ping.main(["ccb-ping"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "codex@1" in out and "gemini@main" in out

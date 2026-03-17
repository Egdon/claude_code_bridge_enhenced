from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

from session_store import load_target_session, session_path_for_target, write_target_session
from target_command_utils import kill_target, mark_target_session_state, resolve_scope_targets_for_command


def _load_module(name: str, path: Path) -> object:
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_ping() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    return _load_module("ccb_ping_target_granularity", repo_root / "bin" / "ccb-ping")


def _load_autonew() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    return _load_module("autonew_target_granularity", repo_root / "bin" / "autonew")


def test_resolve_scope_targets_supports_target_provider_and_all(tmp_path: Path) -> None:
    write_target_session(tmp_path, "codex@1", {"target": "codex@1", "active": True})
    write_target_session(tmp_path, "codex@2", {"target": "codex@2", "active": True})
    write_target_session(tmp_path, "gemini@main", {"target": "gemini@main", "active": True})

    targets, scope = resolve_scope_targets_for_command(tmp_path, positional=["codex@1"], provider=None)
    assert scope == "target"
    assert targets == ["codex@1"]

    targets, scope = resolve_scope_targets_for_command(tmp_path, positional=[], provider="codex")
    assert scope == "provider"
    assert targets == ["codex@1", "codex@2"]

    targets, scope = resolve_scope_targets_for_command(tmp_path, positional=[], provider=None)
    assert scope == "all"
    assert targets == ["codex@1", "codex@2", "gemini@main"]


def test_mark_target_session_state_updates_target_and_matching_legacy_session(tmp_path: Path) -> None:
    write_target_session(tmp_path, "codex@2", {"target": "codex@2", "active": True})

    cfg_dir = tmp_path / ".ccb"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = cfg_dir / ".codex-session"
    legacy_file.write_text(json.dumps({"target": "codex@2", "active": True}), encoding="utf-8")

    mark_target_session_state(tmp_path, "codex@2", active=False, ended_at="2026-03-08 13:00:00")

    target_payload = load_target_session(tmp_path, "codex@2")
    assert target_payload is not None
    assert target_payload["active"] is False
    assert target_payload["ended_at"] == "2026-03-08 13:00:00"

    legacy_payload = json.loads(legacy_file.read_text(encoding="utf-8"))
    assert legacy_payload["active"] is False
    assert legacy_payload["ended_at"] == "2026-03-08 13:00:00"


def test_kill_target_missing_does_not_create_ghost_session(tmp_path: Path) -> None:
    ok, message = kill_target(tmp_path, "codex@99")

    assert ok is False
    assert "no active pane found" in message
    assert not session_path_for_target(tmp_path, "codex@99").exists()
    assert load_target_session(tmp_path, "codex@99") is None



def test_ccb_ping_bare_provider_keeps_legacy_path(monkeypatch, tmp_path: Path) -> None:
    ping = _load_ping()
    monkeypatch.chdir(tmp_path)

    called: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr(
        ping,
        "_run_legacy_provider_ping",
        lambda provider, session_file=None, autostart=False: (called.append((provider, session_file, autostart)) or 0),
    )

    rc = ping.main(["ccb-ping", "codex", "--session-file", "session.json", "--autostart"])

    assert rc == 0
    assert called == [("codex", "session.json", True)]


def test_ccb_ping_provider_scope_pings_each_target(monkeypatch, tmp_path: Path, capsys) -> None:
    ping = _load_ping()
    monkeypatch.chdir(tmp_path)

    called: list[str] = []
    monkeypatch.setattr(ping, "_resolve_scope_targets", lambda work_dir, target=None, provider=None: ["codex@1", "codex@2"])
    monkeypatch.setattr(
        ping,
        "_ping_targets",
        lambda targets, work_dir, autostart=False: ([(target, True, f"[OK] {target}") for target in targets], []),
    )

    rc = ping.main(["ccb-ping", "--provider", "codex"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "codex@1" in captured.out
    assert "codex@2" in captured.out


def test_autonew_provider_scope_resets_all_provider_targets(monkeypatch, tmp_path: Path, capsys) -> None:
    autonew = _load_autonew()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(autonew, "_resolve_scope_targets", lambda work_dir, target=None, provider=None: ["codex@1", "codex@2"])

    sent: list[str] = []
    monkeypatch.setattr(
        autonew,
        "_send_reset_to_target",
        lambda work_dir, target: (sent.append(target) or True, f"Sent /new to {target}"),
    )

    rc = autonew.main(["autonew", "--provider", "codex"])

    assert rc == autonew.EXIT_OK
    assert sent == ["codex@1", "codex@2"]
    captured = capsys.readouterr()
    assert "codex@1" in captured.out
    assert "codex@2" in captured.out


def test_autonew_all_scope_resets_all_targets(monkeypatch, tmp_path: Path) -> None:
    autonew = _load_autonew()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(autonew, "_resolve_scope_targets", lambda work_dir, target=None, provider=None: ["codex@1", "gemini@main"])

    sent: list[str] = []
    monkeypatch.setattr(
        autonew,
        "_send_reset_to_target",
        lambda work_dir, target: (sent.append(target) or True, f"Sent to {target}"),
    )

    rc = autonew.main(["autonew"])

    assert rc == autonew.EXIT_OK
    assert sent == ["codex@1", "gemini@main"]


def test_autonew_bare_provider_uses_legacy_compat_when_registry_exists(monkeypatch, tmp_path: Path, capsys) -> None:
    autonew = _load_autonew()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(autonew, "_resolve_provider_targets", lambda work_dir, provider: ["codex@1", "codex@2"])
    monkeypatch.setattr(
        autonew,
        "load_registry_by_project_id",
        lambda project_id, provider: {
            "terminal": "tmux",
            "providers": {"codex": {"pane_id": "%9"}},
        },
    )

    sent: list[tuple[str, str]] = []

    class _FakeBackend:
        def is_alive(self, pane_id: str) -> bool:
            return True

        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

    monkeypatch.setattr(autonew, "get_backend_for_session", lambda _record: _FakeBackend())

    rc = autonew.main(["autonew", "codex"])

    assert rc == autonew.EXIT_OK
    assert sent == [("%9", "/new")]
    captured = capsys.readouterr()
    assert "codex" in captured.out

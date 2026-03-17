from __future__ import annotations

import json
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import control_plane
from control_plane import record_target_activation
from pane_registry import load_registry_by_session_id, upsert_registry
from project_id import compute_ccb_project_id
from session_store import load_target_session, write_target_session
from session_utils import project_config_dir



def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_add_rm_targets", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_add_rm_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod



def test_cmd_add_rejects_bare_provider(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    rc = ccb.cmd_add(SimpleNamespace(target="codex"))

    assert rc == 2
    assert "provider@instance" in capsys.readouterr().err



def test_cmd_add_reuses_active_session_context_and_parent_pane(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    runtime_root = tmp_path / "runtime-root"
    existing_runtime = runtime_root / "claude"
    existing_runtime.mkdir(parents=True, exist_ok=True)
    write_target_session(
        tmp_path,
        "claude@main",
        {
            "target": "claude@main",
            "session_id": "sess-1",
            "runtime_dir": str(existing_runtime),
            "pane_id": "%9",
            "terminal": "tmux",
            "work_dir": str(tmp_path),
            "active": True,
        },
    )

    called: dict[str, object] = {}

    class _FakeLauncher:
        def __init__(self, *args, **kwargs):
            called["targets"] = kwargs.get("targets")
            self.session_id = "fresh-session"
            self.runtime_dir = tmp_path / "fresh-runtime"
            self.project_root = tmp_path
            self.project_id = "fresh-project"
            self.terminal_type = "wezterm"

        def _start_target(self, target: str, *, parent_pane=None, direction=None):
            called["start"] = {
                "target": target,
                "parent_pane": parent_pane,
                "direction": direction,
                "session_id": self.session_id,
                "runtime_dir": str(self.runtime_dir),
                "project_root": str(self.project_root),
                "project_id": self.project_id,
                "terminal_type": self.terminal_type,
            }
            return "%21"

    monkeypatch.setattr(ccb.target_cmd_utils, "ping_target", lambda work_dir, target: (False, "missing"))
    monkeypatch.setattr(ccb, "request_control_plane_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(ccb, "AILauncher", _FakeLauncher)

    rc = ccb.cmd_add(SimpleNamespace(target="codex@2"))

    assert rc == 0
    assert called == {
        "targets": ["codex@2"],
        "start": {
            "target": "codex@2",
            "parent_pane": "%9",
            "direction": "bottom",
            "session_id": "sess-1",
            "runtime_dir": str(runtime_root),
            "project_root": str(tmp_path),
            "project_id": compute_ccb_project_id(tmp_path),
            "terminal_type": "tmux",
        },
    }



def test_cmd_add_requires_existing_active_target_context(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ccb.target_cmd_utils, "ping_target", lambda work_dir, target: (False, "missing"))
    monkeypatch.setattr(ccb, "request_control_plane_operation", lambda *args, **kwargs: None)

    rc = ccb.cmd_add(SimpleNamespace(target="codex@2"))

    assert rc == 2
    assert "No active CCB target context" in capsys.readouterr().err



def test_cmd_add_prefers_live_control_plane_when_available(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ccb.target_cmd_utils, "ping_target", lambda work_dir, target: (False, "missing"))
    monkeypatch.setattr(
        ccb,
        "request_control_plane_operation",
        lambda work_dir, op, target: {"exit_code": 0, "message": f"✅ live added {target}"},
    )

    class _FailLauncher:
        def __init__(self, *args, **kwargs):
            raise AssertionError("fallback launcher should not be used when live control plane responds")

    monkeypatch.setattr(ccb, "AILauncher", _FailLauncher)

    rc = ccb.cmd_add(SimpleNamespace(target="codex@2"))

    assert rc == 0
    assert "live added codex@2" in capsys.readouterr().out



def test_cmd_add_falls_back_to_control_plane_context(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    control_plane_path = project_config_dir(tmp_path) / "control-plane.json"
    control_plane_path.parent.mkdir(parents=True, exist_ok=True)
    control_plane_path.write_text(
        """
{
  "schema_version": 1,
  "session_id": "sess-ctl",
  "runtime_root": "RUNTIME_ROOT",
  "project_id": "proj-ctl",
  "terminal": "tmux",
  "parent_pane_id": "%77",
  "active_targets": ["claude@main"]
}
""".replace("RUNTIME_ROOT", str(tmp_path / "runtime-root")),
        encoding="utf-8",
    )

    called: dict[str, object] = {}

    class _FakeLauncher:
        def __init__(self, *args, **kwargs):
            called["targets"] = kwargs.get("targets")
            self.session_id = "fresh-session"
            self.runtime_dir = tmp_path / "fresh-runtime"
            self.project_root = tmp_path
            self.project_id = "fresh-project"
            self.terminal_type = "wezterm"

        def _start_target(self, target: str, *, parent_pane=None, direction=None):
            called["start"] = {
                "target": target,
                "parent_pane": parent_pane,
                "direction": direction,
                "session_id": self.session_id,
                "runtime_dir": str(self.runtime_dir),
                "project_root": str(self.project_root),
                "project_id": self.project_id,
                "terminal_type": self.terminal_type,
            }
            return "%88"

    monkeypatch.setattr(ccb.target_cmd_utils, "ping_target", lambda work_dir, target: (False, "missing"))
    monkeypatch.setattr(ccb, "AILauncher", _FakeLauncher)

    rc = ccb.cmd_add(SimpleNamespace(target="codex@3"))

    assert rc == 0
    assert called == {
        "targets": ["codex@3"],
        "start": {
            "target": "codex@3",
            "parent_pane": "%77",
            "direction": "bottom",
            "session_id": "sess-ctl",
            "runtime_dir": str(tmp_path / "runtime-root"),
            "project_root": str(tmp_path),
            "project_id": compute_ccb_project_id(tmp_path),
            "terminal_type": "tmux",
        },
    }



def test_cmd_rm_prefers_live_control_plane_when_available(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ccb,
        "request_control_plane_operation",
        lambda work_dir, op, target: {"exit_code": 0, "message": f"✅ live removed {target}"},
    )
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: (_ for _ in ()).throw(AssertionError("fallback rm should not run")))

    rc = ccb.cmd_rm(SimpleNamespace(target="codex@2"))

    assert rc == 0
    assert "live removed codex@2" in capsys.readouterr().out



def test_cmd_rm_rejects_bare_provider(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    rc = ccb.cmd_rm(SimpleNamespace(target="codex"))

    assert rc == 2
    assert "provider@instance" in capsys.readouterr().err



def test_cmd_rm_shuts_down_provider_daemon_when_last_target(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    killed: list[tuple[Path, str, str]] = []
    shutdowns: list[str] = []

    monkeypatch.setattr(ccb, "request_control_plane_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-1")
    monkeypatch.setattr(
        ccb,
        "_kill_target",
        lambda work_dir, project_id, target: (killed.append((work_dir, project_id, target)) or "codex"),
    )
    monkeypatch.setattr(ccb.target_cmd_utils, "resolve_provider_targets", lambda work_dir, provider: [])
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda provider: shutdowns.append(provider) or True)

    rc = ccb.cmd_rm(SimpleNamespace(target="codex@2"))

    assert rc == 0
    assert killed == [(tmp_path, "proj-1", "codex@2")]
    assert shutdowns == ["codex"]



def test_cmd_rm_updates_control_plane_active_targets(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    control_plane_path = project_config_dir(tmp_path) / "control-plane.json"
    control_plane_path.parent.mkdir(parents=True, exist_ok=True)
    control_plane_path.write_text(
        """
{
  "schema_version": 1,
  "session_id": "sess-ctl",
  "runtime_root": "RUNTIME_ROOT",
  "project_id": "proj-ctl",
  "terminal": "tmux",
  "parent_pane_id": "%22",
  "active_targets": ["codex@1", "codex@2", "claude@main"]
}
""".replace("RUNTIME_ROOT", str(tmp_path / "runtime-root")),
        encoding="utf-8",
    )

    monkeypatch.setattr(ccb, "request_control_plane_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(ccb, "compute_ccb_project_id", lambda _wd: "proj-ctl")
    monkeypatch.setattr(ccb, "_kill_target", lambda work_dir, project_id, target: "codex")
    monkeypatch.setattr(ccb.target_cmd_utils, "resolve_provider_targets", lambda work_dir, provider: ["codex@1"])
    shutdowns: list[str] = []
    monkeypatch.setattr(ccb, "_shutdown_provider_daemon", lambda provider: shutdowns.append(provider) or True)

    rc = ccb.cmd_rm(SimpleNamespace(target="codex@2"))

    assert rc == 0
    payload = json.loads(control_plane_path.read_text(encoding="utf-8"))
    assert payload["active_targets"] == ["codex@1", "claude@main"]
    assert payload["parent_pane_id"] == "%22"
    assert shutdowns == []



def test_record_target_activation_uses_control_plane_lock(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple] = []

    class _FakeLock:
        def __init__(self, provider: str, timeout: float = 60.0, cwd: str | None = None):
            calls.append(("init", provider, timeout, cwd))

        def __enter__(self):
            calls.append(("enter",))
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            calls.append(("exit", exc_type))
            return False

    monkeypatch.setattr(control_plane, "ProviderLock", _FakeLock)

    record_target_activation(
        tmp_path,
        session_id="sess-ctl",
        runtime_root=tmp_path / "runtime-root",
        project_id="proj-ctl",
        terminal="tmux",
        target="codex@1",
        pane_id="%11",
    )

    assert calls[0][0] == "init"
    assert calls[0][1] == "control-plane"
    assert calls[1] == ("enter",)
    assert calls[-1] == ("exit", None)



def test_record_target_removal_prefers_same_provider_parent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime_root = tmp_path / "runtime-root"
    record_target_activation(
        tmp_path,
        session_id="sess-ctl",
        runtime_root=runtime_root,
        project_id="proj-ctl",
        terminal="tmux",
        target="codex@1",
        pane_id="%11",
    )
    record_target_activation(
        tmp_path,
        session_id="sess-ctl",
        runtime_root=runtime_root,
        project_id="proj-ctl",
        terminal="tmux",
        target="claude@main",
        pane_id="%21",
    )
    record_target_activation(
        tmp_path,
        session_id="sess-ctl",
        runtime_root=runtime_root,
        project_id="proj-ctl",
        terminal="tmux",
        target="codex@2",
        pane_id="%12",
    )

    ccb = _load_ccb_module()
    ccb.record_target_removal(tmp_path, "codex@2")

    payload = json.loads((project_config_dir(tmp_path) / "control-plane.json").read_text(encoding="utf-8"))
    assert payload["active_targets"] == ["codex@1", "claude@main"]
    assert payload["parent_target"] == "codex@1"
    assert payload["parent_pane_id"] == "%11"



def test_kill_target_marks_target_session_and_registry_inactive(monkeypatch, tmp_path: Path) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    project_id = compute_ccb_project_id(tmp_path)
    write_target_session(
        tmp_path,
        "codex@2",
        {
            "target": "codex@2",
            "active": True,
            "pane_id": "%2",
            "terminal": "tmux",
            "work_dir": str(tmp_path),
        },
    )
    upsert_registry(
        {
            "ccb_session_id": "sess-1",
            "ccb_project_id": project_id,
            "work_dir": str(tmp_path),
            "terminal": "tmux",
            "instances": {
                "codex@2": {
                    "pane_id": "%2",
                    "active": True,
                    "pane_title_marker": "CCB-Codex@2",
                }
            },
        }
    )

    def _fake_kill_target(work_dir: Path, target: str):
        ccb.target_cmd_utils.mark_target_session_state(
            work_dir,
            target,
            active=False,
            ended_at="2026-03-08 13:00:00",
        )
        return True, f"✅ {target} session terminated"

    monkeypatch.setattr(ccb.target_cmd_utils, "kill_target", _fake_kill_target)

    provider = ccb._kill_target(tmp_path, project_id, "codex@2")

    assert provider == "codex"

    target_payload = load_target_session(tmp_path, "codex@2")
    assert target_payload is not None
    assert target_payload["active"] is False
    assert target_payload["ended_at"] == "2026-03-08 13:00:00"

    registry = load_registry_by_session_id("sess-1")
    assert registry is not None
    assert registry["instances"]["codex@2"]["active"] is False
    assert registry["instances"]["codex@2"]["ended_at"] == "2026-03-08 13:00:00"

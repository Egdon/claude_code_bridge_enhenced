from __future__ import annotations

import importlib.util
import json
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

from project_id import compute_ccb_project_id
from session_store import write_target_session



def _load_mounted_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "bin" / "ccb-mounted"
    loader = SourceFileLoader("ccb_mounted_targets", str(script_path))
    spec = importlib.util.spec_from_loader("ccb_mounted_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod



def test_ccb_mounted_json_reports_distinct_targets(monkeypatch, tmp_path: Path, capsys) -> None:
    mounted = _load_mounted_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mounted, "_resolve_project_targets", lambda work_dir: ["codex@1", "codex@2", "claude@main"])
    monkeypatch.setattr(
        mounted,
        "_probe_target",
        lambda work_dir, target, autostart=False: (target != "claude@main", f"[OK] {target}"),
    )

    rc = mounted.main(["ccb-mounted", "--json"])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["mounted"] == ["codex@1", "codex@2"]
    assert parsed["mounted_providers"] == ["codex"]



def test_ccb_mounted_simple_outputs_space_separated_targets(monkeypatch, tmp_path: Path, capsys) -> None:
    mounted = _load_mounted_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mounted, "_resolve_project_targets", lambda work_dir: ["codex@1", "codex@2"])
    monkeypatch.setattr(mounted, "_probe_target", lambda work_dir, target, autostart=False: (True, f"[OK] {target}"))

    rc = mounted.main(["ccb-mounted", "--simple"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "codex@1 codex@2"



def test_resolve_project_targets_merges_target_sessions_and_registry(tmp_path: Path, monkeypatch) -> None:
    mounted = _load_mounted_module()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_target_session(project_dir, "codex@1", {"target": "codex@1", "active": True})

    registry_dir = tmp_path / ".ccb" / "run"
    registry_dir.mkdir(parents=True, exist_ok=True)
    project_id = compute_ccb_project_id(project_dir)
    registry_path = registry_dir / "ccb-session-s1.json"
    registry_path.write_text(
        json.dumps(
            {
                "ccb_session_id": "s1",
                "ccb_project_id": project_id,
                "work_dir": str(project_dir),
                "terminal": "tmux",
                "updated_at": int(time.time()),
                "instances": {
                    "codex@2": {"pane_id": "%2"},
                    "claude@main": {"pane_id": "%3"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    targets = mounted._resolve_project_targets(project_dir)

    assert targets == ["codex@1", "codex@2", "claude@main"]

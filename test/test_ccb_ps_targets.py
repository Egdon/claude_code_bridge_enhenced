from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

from pane_registry import upsert_registry
from project_id import compute_ccb_project_id
from session_store import write_target_session
from session_utils import project_config_dir



def _load_ccb_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    ccb_path = repo_root / "ccb"
    loader = SourceFileLoader("ccb_script_ps_targets", str(ccb_path))
    spec = importlib.util.spec_from_loader("ccb_script_ps_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod



def test_ccb_ps_lists_target_rows_without_bare_provider_shadow(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    write_target_session(
        tmp_path,
        "codex@1",
        {
            "target": "codex@1",
            "work_dir": str(tmp_path),
            "terminal": "tmux",
            "pane_id": "%11",
            "active": True,
        },
    )
    write_target_session(
        tmp_path,
        "codex@2",
        {
            "target": "codex@2",
            "work_dir": str(tmp_path),
            "terminal": "tmux",
            "pane_id": "%12",
            "active": True,
        },
    )

    cfg_dir = project_config_dir(tmp_path)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / ".codex-session").write_text(
        json.dumps(
            {
                "target": "codex@2",
                "work_dir": str(tmp_path),
                "terminal": "tmux",
                "pane_id": "%12",
                "active": True,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(ccb.sys, "argv", ["ccb", "ps"])
    rc = ccb.main()

    assert rc == 0
    out = capsys.readouterr().out
    target_lines = [line for line in out.splitlines() if line.startswith("codex@")] 
    assert len(target_lines) == 2
    assert any("codex@1" in line and "%11" in line for line in target_lines)
    assert any("codex@2" in line and "%12" in line for line in target_lines)



def test_ccb_ps_reads_explicit_registry_instances_without_bare_provider_shadow(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    assert upsert_registry(
        {
            "ccb_session_id": "sess-ps-1",
            "ccb_project_id": compute_ccb_project_id(tmp_path),
            "work_dir": str(tmp_path),
            "terminal": "tmux",
            "providers": {
                "codex": {"pane_id": "%12", "pane_title_marker": "CCB-Codex@2"},
            },
            "instances": {
                "codex@1": {"pane_id": "%11", "pane_title_marker": "CCB-Codex@1"},
                "codex@2": {"pane_id": "%12", "pane_title_marker": "CCB-Codex@2"},
            },
        }
    )

    monkeypatch.setattr(ccb.sys, "argv", ["ccb", "ps"])
    rc = ccb.main()

    assert rc == 0
    out = capsys.readouterr().out
    target_lines = [line for line in out.splitlines() if line.startswith("codex@")] 
    assert len(target_lines) == 2
    assert any("codex@1" in line and "%11" in line for line in target_lines)
    assert any("codex@2" in line and "%12" in line for line in target_lines)



def test_ccb_ps_falls_back_to_legacy_main_session(monkeypatch, tmp_path: Path, capsys) -> None:
    ccb = _load_ccb_module()
    monkeypatch.chdir(tmp_path)

    cfg_dir = project_config_dir(tmp_path)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / ".gemini-session").write_text(
        json.dumps(
            {
                "work_dir": str(tmp_path),
                "terminal": "tmux",
                "pane_id": "%21",
                "active": True,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(ccb.sys, "argv", ["ccb", "ps"])
    rc = ccb.main()

    assert rc == 0
    out = capsys.readouterr().out
    target_lines = [line for line in out.splitlines() if line.startswith("gemini@")] 
    assert len(target_lines) == 1
    assert "gemini@main" in target_lines[0]
    assert "%21" in target_lines[0]

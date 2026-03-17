from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import pytest

import pane_registry
from pane_registry import get_instances_map, load_registry_by_project_id, load_registry_by_target, upsert_registry
from project_id import compute_ccb_project_id


class _FakeBackend:
    def __init__(self, alive: set[str], marker_map: Optional[dict[str, str]] = None):
        self._alive = set(alive)
        self._marker_map = dict(marker_map or {})

    def is_alive(self, pane_id: str) -> bool:
        return pane_id in self._alive

    def find_pane_by_title_marker(self, marker: str) -> str | None:
        return self._marker_map.get(marker)


def _write_registry_file(home: Path, session_id: str, payload: dict) -> Path:
    path = home / ".ccb" / "run" / f"ccb-session-{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_get_instances_map_projects_provider_entries_to_main_targets() -> None:
    record = {
        "providers": {
            "CoDeX": {"pane_id": "%1", "session_file": "/tmp/.codex-session"},
            "claude": {"pane_id": "%c1"},
        }
    }

    instances = get_instances_map(record)

    assert instances == {
        "codex@main": {"pane_id": "%1", "session_file": "/tmp/.codex-session"},
        "claude@main": {"pane_id": "%c1"},
    }


def test_upsert_registry_merges_instances_and_keeps_provider_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(pane_registry, "get_backend_for_session", lambda _rec: _FakeBackend(alive={"%1", "%2"}))

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    pid = compute_ccb_project_id(work_dir)

    ok1 = upsert_registry(
        {
            "ccb_session_id": "s1",
            "ccb_project_id": pid,
            "work_dir": str(work_dir),
            "terminal": "tmux",
            "instances": {"codex@1": {"pane_id": "%1", "session_file": str(work_dir / ".ccb" / ".codex1-session")}},
        }
    )
    assert ok1 is True

    ok2 = upsert_registry(
        {
            "ccb_session_id": "s1",
            "ccb_project_id": pid,
            "work_dir": str(work_dir),
            "terminal": "tmux",
            "instances": {"codex@2": {"pane_id": "%2", "session_file": str(work_dir / ".ccb" / ".codex2-session")}},
        }
    )
    assert ok2 is True

    reg_path = tmp_path / ".ccb" / "run" / "ccb-session-s1.json"
    data = json.loads(reg_path.read_text(encoding="utf-8"))

    assert set(data["instances"]) == {"codex@1", "codex@2"}
    assert data["providers"]["codex"]["pane_id"] in {"%1", "%2"}


def test_load_registry_by_target_filters_dead_panes_and_matches_exact_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    pid = compute_ccb_project_id(work_dir)

    _write_registry_file(
        tmp_path,
        "new-dead",
        {
            "ccb_session_id": "new-dead",
            "ccb_project_id": pid,
            "work_dir": str(work_dir),
            "terminal": "tmux",
            "updated_at": int(time.time()),
            "instances": {"codex@1": {"pane_id": "%dead"}},
        },
    )
    _write_registry_file(
        tmp_path,
        "old-alive",
        {
            "ccb_session_id": "old-alive",
            "ccb_project_id": pid,
            "work_dir": str(work_dir),
            "terminal": "tmux",
            "updated_at": int(time.time()) - 10,
            "instances": {"codex@1": {"pane_id": "%alive-1"}},
        },
    )
    _write_registry_file(
        tmp_path,
        "other-target",
        {
            "ccb_session_id": "other-target",
            "ccb_project_id": pid,
            "work_dir": str(work_dir),
            "terminal": "tmux",
            "updated_at": int(time.time()) - 1,
            "instances": {"codex@2": {"pane_id": "%alive-2"}},
        },
    )

    monkeypatch.setattr(
        pane_registry,
        "get_backend_for_session",
        lambda _rec: _FakeBackend(alive={"%alive-1", "%alive-2"}),
    )

    rec = load_registry_by_target(pid, "CoDeX@1")
    assert rec is not None
    assert rec.get("ccb_session_id") == "old-alive"


def test_load_registry_by_project_id_supports_instances_only_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    pid = compute_ccb_project_id(work_dir)

    _write_registry_file(
        tmp_path,
        "instances-only",
        {
            "ccb_session_id": "instances-only",
            "ccb_project_id": pid,
            "work_dir": str(work_dir),
            "terminal": "tmux",
            "updated_at": int(time.time()),
            "instances": {"codex@2": {"pane_id": "%alive-2"}},
        },
    )

    monkeypatch.setattr(pane_registry, "get_backend_for_session", lambda _rec: _FakeBackend(alive={"%alive-2"}))

    rec = load_registry_by_project_id(pid, "codex")
    assert rec is not None
    assert rec.get("ccb_session_id") == "instances-only"


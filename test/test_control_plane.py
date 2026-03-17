from __future__ import annotations

import threading
from copy import deepcopy
from pathlib import Path

import control_plane
from control_plane import load_control_plane, record_target_activation, record_target_removal, write_control_plane


def test_record_target_activation_serializes_read_modify_write(monkeypatch, tmp_path: Path) -> None:
    shared: dict[str, object] = {"payload": None}
    shared_lock = threading.Lock()
    start_event = threading.Event()
    load_barrier = threading.Barrier(2, timeout=0.3)

    def fake_load(_work_dir: Path) -> dict[str, object] | None:
        start_event.wait(1.0)
        with shared_lock:
            snapshot = deepcopy(shared["payload"])
        try:
            load_barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return snapshot

    def fake_write(work_dir: Path, payload: dict[str, object]) -> Path:
        with shared_lock:
            shared["payload"] = deepcopy(payload)
        return Path(work_dir) / ".ccb" / "control-plane.json"

    monkeypatch.setattr(control_plane, "load_control_plane", fake_load)
    monkeypatch.setattr(control_plane, "write_control_plane", fake_write)

    errors: list[BaseException] = []

    def activate(target: str, pane_id: str) -> None:
        try:
            record_target_activation(
                tmp_path,
                session_id="sess-1",
                runtime_root=tmp_path / "runtime",
                project_id="proj-1",
                terminal="tmux",
                target=target,
                pane_id=pane_id,
            )
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    thread_one = threading.Thread(target=activate, args=("codex@1", "%1"))
    thread_two = threading.Thread(target=activate, args=("codex@2", "%2"))

    thread_one.start()
    thread_two.start()
    start_event.set()
    thread_one.join()
    thread_two.join()

    assert errors == []
    payload = shared["payload"]
    assert isinstance(payload, dict)
    assert set(payload["active_targets"]) == {"codex@1", "codex@2"}
    assert len(payload["active_targets"]) == 2
    assert payload["target_panes"] == {"codex@1": "%1", "codex@2": "%2"}
    assert payload["parent_target"] in {"codex@1", "codex@2"}
    assert payload["parent_pane_id"] == payload["target_panes"][payload["parent_target"]]



def test_record_target_removal_prefers_same_provider_parent_with_pane(tmp_path: Path) -> None:
    write_control_plane(
        tmp_path,
        {
            "session_id": "sess-1",
            "runtime_root": str(tmp_path / "runtime"),
            "project_id": "proj-1",
            "terminal": "tmux",
            "active_targets": ["codex@1", "codex@2", "gemini@main", "opencode@2"],
            "target_panes": {
                "codex@1": "%1",
                "codex@2": "%2",
                "gemini@main": "%3",
            },
            "parent_target": "codex@2",
            "parent_pane_id": "%2",
        },
    )

    record_target_removal(tmp_path, "codex@2")

    payload = load_control_plane(tmp_path)
    assert payload is not None
    assert payload["active_targets"] == ["codex@1", "gemini@main", "opencode@2"]
    assert payload["parent_target"] == "codex@1"
    assert payload["parent_pane_id"] == "%1"
    assert payload["target_panes"] == {"codex@1": "%1", "gemini@main": "%3"}



def test_record_target_removal_falls_back_to_last_remaining_pane_when_same_provider_missing(tmp_path: Path) -> None:
    write_control_plane(
        tmp_path,
        {
            "session_id": "sess-1",
            "runtime_root": str(tmp_path / "runtime"),
            "project_id": "proj-1",
            "terminal": "tmux",
            "active_targets": ["codex@1", "codex@2", "gemini@main", "opencode@2"],
            "target_panes": {
                "codex@2": "%2",
                "gemini@main": "%3",
            },
            "parent_target": "codex@2",
            "parent_pane_id": "%2",
        },
    )

    record_target_removal(tmp_path, "codex@2")

    payload = load_control_plane(tmp_path)
    assert payload is not None
    assert payload["active_targets"] == ["codex@1", "gemini@main", "opencode@2"]
    assert payload["parent_target"] == "gemini@main"
    assert payload["parent_pane_id"] == "%3"
    assert payload["target_panes"] == {"gemini@main": "%3"}

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_store import (
    list_target_sessions,
    load_target_session,
    session_path_for_target,
    write_target_session,
)


def test_session_path_for_target_uses_provider_instance_layout(tmp_path: Path) -> None:
    path = session_path_for_target(tmp_path, "codex@1")

    assert path == tmp_path.resolve() / ".ccb" / "sessions" / "codex" / "1.json"


def test_session_path_for_target_canonicalizes_target(tmp_path: Path) -> None:
    path = session_path_for_target(tmp_path, " CoDeX@Main ")

    assert path == tmp_path.resolve() / ".ccb" / "sessions" / "codex" / "main.json"


def test_write_target_session_persists_json_and_load_reads_it(tmp_path: Path) -> None:
    payload = {"session_id": "abc", "active": True, "count": 2}

    write_target_session(tmp_path, "codex@1", payload)

    session_path = tmp_path / ".ccb" / "sessions" / "codex" / "1.json"
    assert session_path.exists()
    assert session_path.read_text(encoding="utf-8") == json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    assert load_target_session(tmp_path, "codex@1") == payload


def test_write_target_session_overwrites_existing_payload(tmp_path: Path) -> None:
    write_target_session(tmp_path, "codex@1", {"session_id": "old", "active": False})
    write_target_session(tmp_path, "codex@1", {"session_id": "new", "active": True})

    assert load_target_session(tmp_path, "codex@1") == {"session_id": "new", "active": True}


def test_load_target_session_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_target_session(tmp_path, "codex@missing") is None


def test_load_target_session_returns_none_when_json_is_corrupted(tmp_path: Path) -> None:
    session_path = session_path_for_target(tmp_path, "codex@1")
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text('{"session_id": ', encoding="utf-8")

    assert load_target_session(tmp_path, "codex@1") is None


def test_list_target_sessions_returns_all_targets_sorted_by_target(tmp_path: Path) -> None:
    write_target_session(tmp_path, "codex@2", {"session_id": "c2"})
    write_target_session(tmp_path, "claude@main", {"session_id": "cl"})
    write_target_session(tmp_path, "codex@1", {"session_id": "c1"})

    assert list_target_sessions(tmp_path) == {
        "claude@main": {"session_id": "cl"},
        "codex@1": {"session_id": "c1"},
        "codex@2": {"session_id": "c2"},
    }


def test_list_target_sessions_can_filter_by_provider(tmp_path: Path) -> None:
    write_target_session(tmp_path, "codex@2", {"session_id": "c2"})
    write_target_session(tmp_path, "claude@main", {"session_id": "cl"})
    write_target_session(tmp_path, "codex@1", {"session_id": "c1"})

    assert list_target_sessions(tmp_path, provider="codex") == {
        "codex@1": {"session_id": "c1"},
        "codex@2": {"session_id": "c2"},
    }


def test_list_target_sessions_ignores_non_json_files(tmp_path: Path) -> None:
    write_target_session(tmp_path, "codex@1", {"session_id": "c1"})
    junk_dir = tmp_path / ".ccb" / "sessions" / "codex"
    (junk_dir / "notes.txt").write_text("skip", encoding="utf-8")

    assert list_target_sessions(tmp_path) == {"codex@1": {"session_id": "c1"}}


def test_invalid_target_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        session_path_for_target(tmp_path, "codex")

    with pytest.raises(ValueError):
        write_target_session(tmp_path, "codex", {"session_id": "bad"})

    with pytest.raises(ValueError):
        load_target_session(tmp_path, "codex")


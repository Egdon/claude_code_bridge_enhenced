from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import ctx_transfer_utils
from memory.transfer import ContextTransfer
from session_store import session_path_for_target, write_target_session
from session_utils import project_config_dir


def test_context_transfer_load_session_data_prefers_target_session(tmp_path: Path) -> None:
    cfg = project_config_dir(tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)
    legacy_session = cfg / ".codex-session"
    legacy_session.write_text(
        json.dumps({"codex_session_path": "/tmp/legacy.jsonl", "work_dir": str(tmp_path)}),
        encoding="utf-8",
    )
    write_target_session(
        tmp_path,
        "codex@1",
        {
            "target": "codex@1",
            "codex_session_path": "/tmp/target.jsonl",
            "work_dir": str(tmp_path),
            "active": True,
        },
    )

    transfer = ContextTransfer(work_dir=tmp_path)
    session_file, data = transfer._load_session_data("codex", source_target="codex@1")

    assert session_file == session_path_for_target(tmp_path, "codex@1")
    assert data["target"] == "codex@1"
    assert data["codex_session_path"] == "/tmp/target.jsonl"


def test_context_transfer_load_session_data_non_main_target_does_not_fallback_to_legacy(tmp_path: Path) -> None:
    cfg = project_config_dir(tmp_path)
    cfg.mkdir(parents=True, exist_ok=True)
    legacy_session = cfg / ".codex-session"
    legacy_session.write_text(
        json.dumps({"codex_session_path": "/tmp/legacy.jsonl", "work_dir": str(tmp_path)}),
        encoding="utf-8",
    )

    transfer = ContextTransfer(work_dir=tmp_path)
    session_file, data = transfer._load_session_data("codex", source_target="codex@2")

    assert session_file is None
    assert data == {}


def test_maybe_auto_transfer_isolated_by_target(monkeypatch, tmp_path: Path) -> None:
    ctx_transfer_utils._AUTO_TRANSFER_SEEN.clear()
    session_path = tmp_path / "session.jsonl"
    session_path.write_text("", encoding="utf-8")

    calls: list[tuple[str, object]] = []

    class _FakeContextTransfer:
        def __init__(self, max_tokens: int = 8000, work_dir: Path | None = None):
            calls.append(("init", work_dir))

        def extract_conversations(self, **kwargs):
            calls.append(("extract", kwargs))
            return SimpleNamespace(conversations=[("u", "a")])

        def save_transfer(self, context, fmt, target_provider, filename=None):
            calls.append(("save", filename))
            return tmp_path / f"{filename}.md"

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setenv("CCB_CTX_TRANSFER_ON_SESSION_SWITCH", "1")
    monkeypatch.setattr(ctx_transfer_utils, "_is_current_work_dir", lambda work_dir: True)
    monkeypatch.setattr(ctx_transfer_utils.threading, "Thread", _ImmediateThread)
    monkeypatch.setitem(sys.modules, "memory", SimpleNamespace(ContextTransfer=_FakeContextTransfer))

    ctx_transfer_utils.maybe_auto_transfer(
        provider="codex",
        target="codex@1",
        work_dir=tmp_path,
        session_path=session_path,
        session_id="sid-1",
    )
    ctx_transfer_utils.maybe_auto_transfer(
        provider="codex",
        target="codex@1",
        work_dir=tmp_path,
        session_path=session_path,
        session_id="sid-1",
    )
    ctx_transfer_utils.maybe_auto_transfer(
        provider="codex",
        target="codex@2",
        work_dir=tmp_path,
        session_path=session_path,
        session_id="sid-1",
    )

    extract_calls = [payload for kind, payload in calls if kind == "extract"]
    save_calls = [payload for kind, payload in calls if kind == "save"]

    assert len(extract_calls) == 2
    assert extract_calls[0]["source_target"] == "codex@1"
    assert extract_calls[1]["source_target"] == "codex@2"
    assert len(save_calls) == 2
    assert any("codex--1" in str(filename) for filename in save_calls)
    assert any("codex--2" in str(filename) for filename in save_calls)
    assert all("@" not in str(filename) for filename in save_calls)


def test_save_transfer_default_filename_uses_target_slug(tmp_path: Path) -> None:
    transfer = ContextTransfer(work_dir=tmp_path)
    context = transfer._context_from_pairs(
        [("user", "assistant")],
        provider="codex",
        session_id="session-12345678",
        source_target="codex@2",
    )

    output_path = transfer.save_transfer(context, fmt="markdown")

    assert output_path.name.endswith(".md")
    assert "codex--2" in output_path.name
    assert "@" not in output_path.name

"""
Cursor CLI session management for CCB.

Manages `.cursor-session` files that track the tmux pane and
agent-transcript log path for a running cursor-agent instance.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from ccb_config import apply_backend_env
from project_id import compute_ccb_project_id, normalize_work_dir
from session_store import load_target_session, session_path_for_target
from session_utils import find_project_session_file, safe_write_session
from target_id import instance_of, validate_target
from terminal import get_backend_for_session

apply_backend_env()

CURSOR_TRANSCRIPTS_DIR = Path.home() / ".cursor" / "projects"


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _infer_work_dir_from_session_file(session_file: Path) -> Path:
    try:
        parent = Path(session_file).parent
    except Exception:
        return Path.cwd()
    if parent.name in (".ccb", ".ccb_config"):
        return parent.parent
    return parent


def _ensure_work_dir_fields(data: dict, *, session_file: Path, fallback_work_dir: Optional[Path] = None) -> None:
    if not isinstance(data, dict):
        return

    work_dir_raw = data.get("work_dir")
    work_dir = work_dir_raw.strip() if isinstance(work_dir_raw, str) else ""
    if not work_dir:
        base = fallback_work_dir or _infer_work_dir_from_session_file(session_file)
        work_dir = str(base)
        data["work_dir"] = work_dir

    work_dir_norm_raw = data.get("work_dir_norm")
    work_dir_norm = work_dir_norm_raw.strip() if isinstance(work_dir_norm_raw, str) else ""
    if not work_dir_norm:
        try:
            data["work_dir_norm"] = normalize_work_dir(work_dir)
        except Exception:
            data["work_dir_norm"] = work_dir

    if not str(data.get("ccb_project_id") or "").strip():
        try:
            data["ccb_project_id"] = compute_ccb_project_id(Path(work_dir))
        except Exception:
            pass


@dataclass
class CursorProjectSession:
    session_file: Path
    data: dict

    @property
    def terminal(self) -> str:
        return (self.data.get("terminal") or "tmux").strip() or "tmux"

    @property
    def pane_id(self) -> str:
        v = self.data.get("pane_id")
        if not v and self.terminal == "tmux":
            v = self.data.get("tmux_session")
        return str(v or "").strip()

    @property
    def pane_title_marker(self) -> str:
        return str(self.data.get("pane_title_marker") or "").strip()

    @property
    def cursor_session_id(self) -> str:
        return str(self.data.get("cursor_session_id") or "").strip()

    @property
    def cursor_session_path(self) -> str:
        return str(self.data.get("cursor_session_path") or "").strip()

    @property
    def work_dir(self) -> str:
        return str(self.data.get("work_dir") or self.session_file.parent)

    def backend(self):
        return get_backend_for_session(self.data)

    def ensure_pane(self) -> Tuple[bool, str]:
        backend = self.backend()
        if not backend:
            return False, "Terminal backend not available"

        pane_id = self.pane_id
        if pane_id and backend.is_alive(pane_id):
            return True, pane_id

        marker = self.pane_title_marker
        resolver = getattr(backend, "find_pane_by_title_marker", None)
        if marker and callable(resolver):
            resolved = resolver(marker)
            if resolved and backend.is_alive(str(resolved)):
                self.data["pane_id"] = str(resolved)
                self.data["updated_at"] = _now_str()
                self._write_back()
                return True, str(resolved)

        return False, f"Pane not alive: {pane_id}"

    def update_cursor_binding(self, *, session_path: Optional[Path], session_id: Optional[str]) -> None:
        updated = False
        session_path_str = ""
        if session_path:
            try:
                session_path_str = str(Path(session_path).expanduser())
            except Exception:
                session_path_str = str(session_path)
            if session_path_str and self.data.get("cursor_session_path") != session_path_str:
                self.data["cursor_session_path"] = session_path_str
                updated = True

        if session_id and self.data.get("cursor_session_id") != session_id:
            self.data["cursor_session_id"] = session_id
            updated = True

        if updated:
            self.data["updated_at"] = _now_str()
            if self.data.get("active") is False:
                self.data["active"] = True
            self._write_back()

    def _write_back(self) -> None:
        _ensure_work_dir_fields(self.data, session_file=self.session_file)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        safe_write_session(self.session_file, payload)


def load_project_session(work_dir: Path, target: str | None = None) -> Optional[CursorProjectSession]:
    canonical_target = None
    if target:
        try:
            canonical_target = validate_target(target)
        except ValueError:
            return None
        data = load_target_session(work_dir, canonical_target)
        if data:
            data.setdefault("target", canonical_target)
            session_file = session_path_for_target(work_dir, canonical_target)
            data.setdefault("work_dir", str(work_dir))
            if not data.get("ccb_project_id"):
                try:
                    data["ccb_project_id"] = compute_ccb_project_id(Path(data.get("work_dir") or work_dir))
                except Exception:
                    pass
            _ensure_work_dir_fields(data, session_file=session_file, fallback_work_dir=work_dir)
            return CursorProjectSession(session_file=session_file, data=data)
        if instance_of(canonical_target) != "main":
            return None

    session_file = find_project_session_file(work_dir, ".cursor-session")
    if not session_file:
        return None
    try:
        with session_file.open("r", encoding="utf-8-sig", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or not data:
        return None

    data.setdefault("work_dir", str(work_dir))
    if not data.get("ccb_project_id"):
        try:
            data["ccb_project_id"] = compute_ccb_project_id(Path(data.get("work_dir") or work_dir))
        except Exception:
            pass
    if canonical_target:
        data.setdefault("target", canonical_target)

    _ensure_work_dir_fields(data, session_file=session_file, fallback_work_dir=work_dir)
    return CursorProjectSession(session_file=session_file, data=data)


def compute_session_key(session: CursorProjectSession, target: str | None = None) -> str:
    pid = str(session.data.get("ccb_project_id") or "").strip()
    if not pid:
        try:
            pid = compute_ccb_project_id(Path(session.work_dir))
        except Exception:
            pid = ""
    instance = "main"
    target_value = target or str(session.data.get("target") or "").strip()
    if target_value:
        try:
            instance = instance_of(validate_target(target_value))
        except ValueError:
            instance = "main"
    return f"cursor:{pid}:{instance}" if pid else "cursor:unknown"

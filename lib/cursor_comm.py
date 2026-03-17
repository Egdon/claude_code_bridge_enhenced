"""
Cursor CLI log reader for CCB.

Reads cursor-agent JSONL transcripts stored at:
  ~/.cursor/projects/<project-key>/agent-transcripts/<uuid>.jsonl

JSONL format per line:
  {"role": "user"|"assistant", "message": {"content": [{"type": "text", "text": "..."}]}}
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CURSOR_PROJECTS_ROOT = Path.home() / ".cursor" / "projects"


def _extract_text(entry: dict) -> str:
    """Extract text content from a cursor-agent JSONL entry."""
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _find_latest_transcript(project_dir: Path) -> Optional[Path]:
    """Find the most recently modified transcript JSONL in a project's agent-transcripts dir."""
    transcripts_dir = project_dir / "agent-transcripts"
    if not transcripts_dir.is_dir():
        return None
    candidates = []
    for p in transcripts_dir.iterdir():
        if p.suffix == ".jsonl" and p.is_file():
            candidates.append(p)
    if not candidates:
        # Check nested subdirs (Cursor sometimes nests transcripts under uuid/)
        for sub in transcripts_dir.iterdir():
            if sub.is_dir():
                for p in sub.iterdir():
                    if p.suffix == ".jsonl" and p.is_file() and p.stem == sub.name:
                        candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _project_key_for_work_dir(work_dir: Path) -> Optional[str]:
    """
    Derive the Cursor project key from a work directory.
    Cursor uses a slug like 'home-egdon-workspace-project' for ~/.cursor/projects/
    """
    try:
        resolved = work_dir.resolve()
    except Exception:
        return None
    slug = str(resolved).replace("/", "-").lstrip("-")
    return slug


class CursorLogReader:
    """
    Incremental reader for cursor-agent JSONL transcripts.
    """

    def __init__(
        self,
        work_dir: Optional[Path] = None,
        root: Path = CURSOR_PROJECTS_ROOT,
    ):
        self._root = root
        self._work_dir = work_dir
        self._preferred_session: Optional[Path] = None

    def set_preferred_session(self, path: Path) -> None:
        self._preferred_session = Path(path)

    def current_session_path(self) -> Optional[Path]:
        if self._preferred_session and self._preferred_session.exists():
            return self._preferred_session
        return self._resolve_session_path()

    def _resolve_session_path(self) -> Optional[Path]:
        if not self._work_dir:
            return None
        key = _project_key_for_work_dir(self._work_dir)
        if not key:
            return None
        project_dir = self._root / key
        if not project_dir.is_dir():
            return None
        return _find_latest_transcript(project_dir)

    def capture_state(self) -> Dict[str, Any]:
        session_path = self.current_session_path()
        offset = 0
        if session_path and session_path.exists():
            try:
                offset = session_path.stat().st_size
            except OSError:
                offset = 0
        return {
            "session_path": session_path,
            "offset": offset,
            "carry": b"",
        }

    def _read_new_entries(self, state: dict) -> Tuple[List[Tuple[str, str]], dict]:
        """Read new JSONL entries since last state. Returns (events, new_state)."""
        session_path = state.get("session_path")
        if not session_path:
            session_path = self.current_session_path()
            if not session_path:
                return [], state

        offset = state.get("offset", 0)
        carry = state.get("carry", b"")

        try:
            size = session_path.stat().st_size
        except OSError:
            return [], state

        if size <= offset and not carry:
            return [], state

        events: List[Tuple[str, str]] = []
        try:
            with open(session_path, "rb") as f:
                f.seek(offset)
                raw = carry + f.read()
                new_offset = f.tell()
        except Exception:
            return [], state

        new_carry = b""
        lines = raw.split(b"\n")
        if not raw.endswith(b"\n"):
            new_carry = lines[-1]
            lines = lines[:-1]

        for line_bytes in lines:
            line = line_bytes.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            role = entry.get("role", "")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(entry)
            if text:
                events.append((role, text))

        new_state = {
            "session_path": session_path,
            "offset": new_offset - len(new_carry),
            "carry": new_carry,
        }
        return events, new_state

    def wait_for_events(
        self, state: dict, timeout: float
    ) -> Tuple[List[Tuple[str, str]], dict]:
        """Block until new events appear or timeout."""
        deadline = time.time() + timeout
        while True:
            events, new_state = self._read_new_entries(state)
            if events:
                return events, new_state
            state = new_state
            remaining = deadline - time.time()
            if remaining <= 0:
                return [], state
            time.sleep(min(remaining, 0.3))

    def latest_message(self) -> Optional[str]:
        """Return the text of the latest assistant message."""
        session_path = self.current_session_path()
        if not session_path or not session_path.exists():
            return None
        try:
            with open(session_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return None
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("role") == "assistant":
                text = _extract_text(entry)
                if text:
                    return text
        return None

    def latest_conversations(self, n: int = 1) -> List[Tuple[str, str]]:
        """Return the latest n (question, answer) pairs."""
        session_path = self.current_session_path()
        if not session_path or not session_path.exists():
            return []
        try:
            with open(session_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return []

        entries: List[Tuple[str, str]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            role = entry.get("role", "")
            text = _extract_text(entry)
            if role in ("user", "assistant") and text:
                entries.append((role, text))

        pairs: List[Tuple[str, str]] = []
        i = len(entries) - 1
        while i >= 0 and len(pairs) < n:
            if entries[i][0] == "assistant":
                answer = entries[i][1]
                question = ""
                if i > 0 and entries[i - 1][0] == "user":
                    question = entries[i - 1][1]
                    i -= 1
                pairs.append((question, answer))
            i -= 1
        pairs.reverse()
        return pairs

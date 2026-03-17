"""
Cursor CLI protocol helpers for CCB.

Reuses the core CCB protocol markers (CCB_REQ_ID, CCB_BEGIN, CCB_DONE)
with a wrapper tailored for cursor-agent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ccb_protocol import (
    BEGIN_PREFIX,
    DONE_PREFIX,
    REQ_ID_PREFIX,
    is_done_text,
    make_req_id,
    strip_done_text,
)
from laskd_protocol import extract_reply_for_req


def _language_hint() -> str:
    lang = (os.environ.get("CCB_REPLY_LANG") or os.environ.get("CCB_LANG") or "").strip().lower()
    if lang in {"zh", "cn", "chinese"}:
        return "Reply in Chinese."
    if lang in {"en", "english"}:
        return "Reply in English."
    return ""


def wrap_cursor_prompt(message: str, req_id: str) -> str:
    """Wrap a message with CCB protocol markers for cursor-agent."""
    message = (message or "").rstrip()
    extra_lines: list[str] = []
    lang_hint = _language_hint()
    if lang_hint:
        extra_lines.append(lang_hint)
    extra = "\n".join(extra_lines).strip()
    if extra:
        extra = f"{extra}\n\n"
    return (
        f"{REQ_ID_PREFIX} {req_id}\n\n"
        f"{message}\n\n"
        f"{extra}"
        "Reply using exactly this format:\n"
        f"{BEGIN_PREFIX} {req_id}\n"
        "<reply>\n"
        f"{DONE_PREFIX} {req_id}\n"
    )


@dataclass(frozen=True)
class UaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    output_path: str | None = None
    req_id: str | None = None
    no_wrap: bool = False


@dataclass(frozen=True)
class UaskdResult:
    exit_code: int
    reply: str
    req_id: str
    session_key: str
    done_seen: bool
    done_ms: int | None = None
    anchor_seen: bool = False
    fallback_scan: bool = False
    anchor_ms: int | None = None


__all__ = [
    "wrap_cursor_prompt",
    "extract_reply_for_req",
    "UaskdRequest",
    "UaskdResult",
    "make_req_id",
    "is_done_text",
    "strip_done_text",
]

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from session_utils import project_config_dir, safe_write_session
from target_id import split_target, validate_target

SessionPayload = dict[str, Any]


def session_path_for_target(work_dir: Path | str, target: str) -> Path:
    provider, instance = split_target(target)
    return project_config_dir(Path(work_dir)) / "sessions" / provider / f"{instance}.json"



def write_target_session(work_dir: Path | str, target: str, payload: SessionPayload) -> Path:
    canonical_target = validate_target(target)
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    session_path = session_path_for_target(work_dir, canonical_target)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ok, err = safe_write_session(session_path, content)
    if not ok:
        raise OSError(err or f"failed to write session for {canonical_target}")
    return session_path



def load_target_session(work_dir: Path | str, target: str) -> SessionPayload | None:
    session_path = session_path_for_target(work_dir, target)
    if not session_path.is_file():
        return None

    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        raise ValueError(f"session payload must be a JSON object: {target!r}")
    return data



def list_target_sessions(work_dir: Path | str, provider: str | None = None) -> dict[str, SessionPayload]:
    sessions_root = project_config_dir(Path(work_dir)) / "sessions"
    if not sessions_root.is_dir():
        return {}

    provider_dirs: list[Path]
    if provider is None:
        provider_dirs = sorted((path for path in sessions_root.iterdir() if path.is_dir()), key=lambda path: path.name)
    else:
        provider_name, _instance = split_target(f"{str(provider or '').strip()}@main")
        provider_dir = sessions_root / provider_name
        provider_dirs = [provider_dir] if provider_dir.is_dir() else []

    results: dict[str, SessionPayload] = {}
    for provider_dir in provider_dirs:
        for session_file in sorted(provider_dir.glob("*.json"), key=lambda path: path.stem):
            target = f"{provider_dir.name}@{session_file.stem}"
            try:
                canonical_target = validate_target(target)
            except ValueError:
                continue
            payload = load_target_session(work_dir, canonical_target)
            if payload is not None:
                results[canonical_target] = payload
    return results

from __future__ import annotations

import json
import time
from pathlib import Path

from pane_registry import _get_providers_map, get_instances_map, load_registry_by_project_id, load_registry_by_target
from project_id import compute_ccb_project_id
from session_store import load_target_session, list_target_sessions, write_target_session
from session_utils import find_project_session_file, safe_write_session
from target_id import instance_of, provider_of, validate_target
from terminal import get_backend_for_session

KNOWN_PROVIDERS = ("codex", "gemini", "opencode", "claude", "droid", "cursor")


def _canonical_provider(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw not in KNOWN_PROVIDERS:
        raise ValueError(f"invalid provider: {value!r}")
    return raw


def _legacy_session_file(work_dir: Path | str, provider: str) -> Path | None:
    canonical_provider = _canonical_provider(provider)
    return find_project_session_file(Path(work_dir), f".{canonical_provider}-session")


def _load_json_file(path: Path | None) -> dict | None:
    if not path or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_payload_active(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return payload.get("active", True) is not False


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _list_active_targets_from_sessions(work_dir: Path | str, provider: str | None = None) -> list[str]:
    sessions = list_target_sessions(work_dir, provider)
    targets = [target for target, payload in sessions.items() if _is_payload_active(payload)]
    if targets:
        return targets

    providers = [_canonical_provider(provider)] if provider else list(KNOWN_PROVIDERS)
    legacy_targets: list[str] = []
    for provider_name in providers:
        payload = _load_json_file(_legacy_session_file(work_dir, provider_name))
        if not _is_payload_active(payload):
            continue
        target = payload.get("target") if isinstance(payload, dict) else None
        if isinstance(target, str) and "@" in target:
            try:
                legacy_targets.append(validate_target(target))
                continue
            except ValueError:
                pass
        legacy_targets.append(f"{provider_name}@main")
    return _dedupe_keep_order(legacy_targets)


def resolve_provider_targets(work_dir: Path | str, provider: str) -> list[str]:
    return _list_active_targets_from_sessions(work_dir, _canonical_provider(provider))


def resolve_scope_targets_for_command(
    work_dir: Path | str,
    positional: list[str] | tuple[str, ...] | None = None,
    provider: str | None = None,
) -> tuple[list[str], str]:
    raw_positional = [str(item).strip() for item in (positional or []) if str(item).strip()]
    if provider:
        return resolve_provider_targets(work_dir, provider), "provider"

    explicit_targets: list[str] = []
    bare_providers: list[str] = []
    for item in raw_positional:
        if "@" in item:
            explicit_targets.append(validate_target(item))
        else:
            bare_providers.append(_canonical_provider(item))

    if explicit_targets:
        return _dedupe_keep_order(explicit_targets), "target"

    if bare_providers:
        targets: list[str] = []
        for provider_name in bare_providers:
            targets.extend(resolve_provider_targets(work_dir, provider_name))
        return _dedupe_keep_order(targets), "provider"

    return _list_active_targets_from_sessions(work_dir), "all"


def resolve_target_entry(work_dir: Path | str, target: str) -> tuple[dict | None, dict | None]:
    canonical_target = validate_target(target)
    project_root = Path(work_dir)

    try:
        project_id = compute_ccb_project_id(project_root)
    except Exception:
        project_id = ""

    record: dict | None = None
    if project_id:
        record = load_registry_by_target(project_id, canonical_target)
        if record is None and instance_of(canonical_target) == "main":
            record = load_registry_by_project_id(project_id, provider_of(canonical_target))

    if isinstance(record, dict):
        instances_map = get_instances_map(record)
        entry = instances_map.get(canonical_target)
        if isinstance(entry, dict):
            return record, entry
        provider_entry = _get_providers_map(record).get(provider_of(canonical_target))
        if isinstance(provider_entry, dict):
            return record, provider_entry

    target_session = load_target_session(project_root, canonical_target)
    if isinstance(target_session, dict):
        return target_session, target_session

    if instance_of(canonical_target) == "main":
        legacy_payload = _load_json_file(_legacy_session_file(project_root, provider_of(canonical_target)))
        if isinstance(legacy_payload, dict):
            legacy_payload.setdefault("target", canonical_target)
            return legacy_payload, legacy_payload

    return None, None


def _resolve_pane_id(record: dict, entry: dict, backend) -> str:
    pane_id = str(
        entry.get("pane_id")
        or record.get("pane_id")
        or entry.get("tmux_session")
        or record.get("tmux_session")
        or ""
    ).strip()
    marker = str(entry.get("pane_title_marker") or record.get("pane_title_marker") or "").strip()
    if (not pane_id) and marker:
        resolver = getattr(backend, "find_pane_by_title_marker", None)
        if callable(resolver):
            try:
                pane_id = str(resolver(marker) or "").strip()
            except Exception:
                pane_id = ""
    return pane_id


def mark_target_session_state(
    work_dir: Path | str,
    target: str,
    *,
    active: bool,
    ended_at: str | None = None,
) -> None:
    canonical_target = validate_target(target)
    payload = load_target_session(work_dir, canonical_target) or {"target": canonical_target}
    payload["target"] = canonical_target
    payload["active"] = bool(active)
    if ended_at is not None:
        payload["ended_at"] = ended_at
    write_target_session(work_dir, canonical_target, payload)

    legacy_path = _legacy_session_file(work_dir, provider_of(canonical_target))
    legacy_payload = _load_json_file(legacy_path)
    if not isinstance(legacy_payload, dict):
        return

    legacy_target = str(legacy_payload.get("target") or "").strip()
    matches_target = False
    if legacy_target:
        try:
            matches_target = validate_target(legacy_target) == canonical_target
        except ValueError:
            matches_target = False
    elif instance_of(canonical_target) == "main":
        matches_target = True

    if not matches_target:
        return

    legacy_payload["target"] = canonical_target
    legacy_payload["active"] = bool(active)
    if ended_at is not None:
        legacy_payload["ended_at"] = ended_at
    safe_write_session(legacy_path, json.dumps(legacy_payload, ensure_ascii=False, indent=2))


def ping_target(work_dir: Path | str, target: str) -> tuple[bool, str]:
    canonical_target = validate_target(target)
    record, entry = resolve_target_entry(work_dir, canonical_target)
    if not record or not entry:
        return False, f"[FAIL] {canonical_target}: no active session"

    backend = get_backend_for_session(record)
    if not backend:
        return False, f"[FAIL] {canonical_target}: terminal backend unavailable"

    pane_id = _resolve_pane_id(record, entry, backend)
    if not pane_id:
        return False, f"[FAIL] {canonical_target}: pane id missing"

    try:
        if backend.is_alive(pane_id):
            return True, f"[OK] {canonical_target} pane {pane_id} is alive"
        return False, f"[FAIL] {canonical_target} pane {pane_id} is not alive"
    except Exception as exc:
        return False, f"[FAIL] {canonical_target}: {exc}"


def send_text_to_target(work_dir: Path | str, target: str, text: str) -> tuple[bool, str]:
    canonical_target = validate_target(target)
    record, entry = resolve_target_entry(work_dir, canonical_target)
    if not record or not entry:
        return False, f"[ERROR] No active {canonical_target} session found for this project."

    backend = get_backend_for_session(record)
    if not backend:
        return False, "[ERROR] Terminal backend not available."

    pane_id = _resolve_pane_id(record, entry, backend)
    if not pane_id:
        return False, f"[ERROR] No pane_id found for {canonical_target}."

    try:
        if not backend.is_alive(pane_id):
            return False, f"[ERROR] {canonical_target} pane {pane_id} is not alive."
    except Exception as exc:
        return False, f"[ERROR] Failed to check pane status: {exc}"

    try:
        backend.send_text(pane_id, text)
        return True, f"Sent {text} to {canonical_target}"
    except Exception as exc:
        return False, f"[ERROR] Failed to send {text}: {exc}"


def kill_target(work_dir: Path | str, target: str) -> tuple[bool, str]:
    canonical_target = validate_target(target)
    record, entry = resolve_target_entry(work_dir, canonical_target)
    ended_at = time.strftime("%Y-%m-%d %H:%M:%S")

    if record is None and entry is None:
        return False, f"ℹ️  {canonical_target}: no active pane found"
    if not record or not entry:
        mark_target_session_state(work_dir, canonical_target, active=False, ended_at=ended_at)
        return False, f"ℹ️  {canonical_target}: no active pane found"

    backend = get_backend_for_session(record)
    if not backend:
        mark_target_session_state(work_dir, canonical_target, active=False, ended_at=ended_at)
        return False, f"❌ {canonical_target}: terminal backend not available"

    pane_id = _resolve_pane_id(record, entry, backend)
    if not pane_id:
        mark_target_session_state(work_dir, canonical_target, active=False, ended_at=ended_at)
        return False, f"❌ {canonical_target}: pane id missing"

    try:
        if backend.is_alive(pane_id):
            backend.kill_pane(pane_id)
        mark_target_session_state(work_dir, canonical_target, active=False, ended_at=ended_at)
        return True, f"✅ {canonical_target} session terminated"
    except Exception as exc:
        return False, f"❌ {canonical_target}: {exc}"

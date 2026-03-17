from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, Iterable

from cli_output import atomic_write_text
from project_id import compute_ccb_project_id
from target_id import instance_of, provider_of, validate_target
from terminal import get_backend_for_session

REGISTRY_PREFIX = "ccb-session-"
REGISTRY_SUFFIX = ".json"
REGISTRY_TTL_SECONDS = 7 * 24 * 60 * 60


def _debug_enabled() -> bool:
    return os.environ.get("CCB_DEBUG") in ("1", "true", "yes")


def _debug(message: str) -> None:
    if not _debug_enabled():
        return
    print(f"[DEBUG] {message}", file=sys.stderr)


def _registry_dir() -> Path:
    return Path.home() / ".ccb" / "run"


def registry_path_for_session(session_id: str) -> Path:
    return _registry_dir() / f"{REGISTRY_PREFIX}{session_id}{REGISTRY_SUFFIX}"


def _iter_registry_files() -> Iterable[Path]:
    registry_dir = _registry_dir()
    if not registry_dir.exists():
        return []
    return sorted(registry_dir.glob(f"{REGISTRY_PREFIX}*{REGISTRY_SUFFIX}"))


def _coerce_updated_at(value: Any, fallback_path: Optional[Path] = None) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.isdigit():
            try:
                return int(trimmed)
            except ValueError:
                pass
    if fallback_path:
        try:
            return int(fallback_path.stat().st_mtime)
        except OSError:
            return 0
    return 0


def _is_stale(updated_at: int, now: Optional[int] = None) -> bool:
    if updated_at <= 0:
        return True
    now_ts = int(time.time()) if now is None else int(now)
    return (now_ts - updated_at) > REGISTRY_TTL_SECONDS


def _load_registry_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        _debug(f"Failed to read registry {path}: {exc}")
    return None


def _provider_entry_from_legacy(data: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """
    Best-effort migration from legacy flat keys to providers.<provider>.*
    """
    provider = (provider or "").strip().lower()
    out: Dict[str, Any] = {}

    if provider == "codex":
        for k_src, k_dst in [
            ("codex_pane_id", "pane_id"),
            ("pane_title_marker", "pane_title_marker"),
            ("codex_session_id", "codex_session_id"),
            ("codex_session_path", "codex_session_path"),
        ]:
            v = data.get(k_src)
            if v:
                out[k_dst] = v
    elif provider == "gemini":
        for k_src, k_dst in [
            ("gemini_pane_id", "pane_id"),
            ("pane_title_marker", "pane_title_marker"),
            ("gemini_session_id", "gemini_session_id"),
            ("gemini_session_path", "gemini_session_path"),
        ]:
            v = data.get(k_src)
            if v:
                out[k_dst] = v
    elif provider == "opencode":
        for k_src, k_dst in [
            ("opencode_pane_id", "pane_id"),
            ("pane_title_marker", "pane_title_marker"),
        ]:
            v = data.get(k_src)
            if v:
                out[k_dst] = v
    elif provider == "claude":
        v = data.get("claude_pane_id")
        if v:
            out["pane_id"] = v

    return out


def _default_target_for_provider(provider: str) -> str:
    prov = (provider or "").strip().lower()
    if not prov:
        return ""
    return f"{prov}@main"


def _get_explicit_providers_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    providers = data.get("providers")
    if isinstance(providers, dict):
        out: Dict[str, Dict[str, Any]] = {}
        for key, value in providers.items():
            if isinstance(key, str) and isinstance(value, dict):
                out[key.strip().lower()] = dict(value)
        return out

    out = {}
    for provider in ("codex", "gemini", "opencode", "claude"):
        entry = _provider_entry_from_legacy(data, provider)
        if entry:
            out[provider] = entry
    return out


def _get_explicit_instances_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    instances = data.get("instances")
    if not isinstance(instances, dict):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for target, entry in instances.items():
        if not isinstance(target, str) or not isinstance(entry, dict):
            continue
        try:
            canonical_target = validate_target(target)
        except ValueError:
            _debug(f"Ignoring invalid registry target {target!r}")
            continue
        out[canonical_target] = dict(entry)
    return out


def get_instances_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out = _get_explicit_instances_map(data)

    for provider, entry in _get_explicit_providers_map(data).items():
        target = _default_target_for_provider(provider)
        if target and target not in out:
            out[target] = dict(entry)

    return out


def _instance_projection_sort_key(item: tuple[str, Dict[str, Any]]) -> tuple[str, int, str]:
    target, _entry = item
    return provider_of(target), 0 if instance_of(target) == "main" else 1, target


def _get_providers_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    providers = _get_explicit_providers_map(data)

    for target, entry in sorted(get_instances_map(data).items(), key=_instance_projection_sort_key):
        provider = provider_of(target)
        providers.setdefault(provider, dict(entry))

    return providers


def _entry_pane_alive(record: Dict[str, Any], entry: Dict[str, Any]) -> bool:
    pane_id = str(entry.get("pane_id") or "").strip()
    marker = str(entry.get("pane_title_marker") or "").strip()

    backend = None
    try:
        backend = get_backend_for_session({"terminal": record.get("terminal", "tmux")})
    except Exception:
        backend = None
    if not backend:
        return False

    if (not pane_id) and marker:
        resolver = getattr(backend, "find_pane_by_title_marker", None)
        if callable(resolver):
            try:
                pane_id = str(resolver(marker) or "").strip()
            except Exception:
                pane_id = ""

    if not pane_id:
        return False

    try:
        return bool(backend.is_alive(pane_id))
    except Exception:
        return False


def _provider_pane_alive(record: Dict[str, Any], provider: str) -> bool:
    prov = (provider or "").strip().lower()
    if not prov:
        return False

    for target, entry in get_instances_map(record).items():
        if provider_of(target) != prov:
            continue
        if _entry_pane_alive(record, entry):
            return True

    return False


def _target_pane_alive(record: Dict[str, Any], target: str) -> bool:
    try:
        canonical_target = validate_target(target)
    except ValueError:
        return False

    entry = get_instances_map(record).get(canonical_target)
    if not isinstance(entry, dict):
        return False
    return _entry_pane_alive(record, entry)


def _effective_project_id(data: Dict[str, Any]) -> tuple[str, bool]:
    existing = (data.get("ccb_project_id") or "").strip()
    inferred = ""
    if not existing:
        wd = (data.get("work_dir") or "").strip()
        if wd:
            try:
                inferred = compute_ccb_project_id(Path(wd))
            except Exception:
                inferred = ""
    effective = existing or inferred
    needs_migration = (not existing) and bool(inferred)
    return effective, needs_migration


def _maybe_persist_project_id(record: Dict[str, Any]) -> None:
    try:
        if (record.get("ccb_project_id") or "").strip():
            return
        wd = (record.get("work_dir") or "").strip()
        if not wd:
            return
        record["ccb_project_id"] = compute_ccb_project_id(Path(wd))
        upsert_registry(record)
    except Exception:
        pass


def _scoped_entry_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in record.items():
        if value is None:
            continue
        if key in {"pane_id", "pane_title_marker", "session_file"} or key.endswith("_session_id") or key.endswith("_session_path") or key.endswith("_project_id"):
            out[key] = value
    return out


def _merge_entry(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for key, value in src.items():
        if value is None:
            continue
        dst[key] = value


def load_registry_by_session_id(session_id: str) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    path = registry_path_for_session(session_id)
    if not path.exists():
        return None
    data = _load_registry_file(path)
    if not data:
        return None
    updated_at = _coerce_updated_at(data.get("updated_at"), path)
    if _is_stale(updated_at):
        _debug(f"Registry stale for session {session_id}: {path}")
        return None
    return data


def load_registry_by_claude_pane(pane_id: str) -> Optional[Dict[str, Any]]:
    if not pane_id:
        return None
    best: Optional[Dict[str, Any]] = None
    best_ts = -1
    for path in _iter_registry_files():
        data = _load_registry_file(path)
        if not data:
            continue

        matched = False
        for target, entry in get_instances_map(data).items():
            if provider_of(target) != "claude":
                continue
            if str(entry.get("pane_id") or "").strip() == pane_id:
                matched = True
                break
        if not matched and data.get("claude_pane_id") != pane_id:
            continue

        updated_at = _coerce_updated_at(data.get("updated_at"), path)
        if _is_stale(updated_at):
            _debug(f"Registry stale for pane {pane_id}: {path}")
            continue
        if updated_at > best_ts:
            best = data
            best_ts = updated_at
    return best


def load_registry_by_project_id(ccb_project_id: str, provider: str) -> Optional[Dict[str, Any]]:
    """
    Load the newest alive registry record matching `{ccb_project_id, provider}`.

    This enforces directory isolation and avoids parent-directory pollution.
    """
    proj = (ccb_project_id or "").strip()
    prov = (provider or "").strip().lower()
    if not proj or not prov:
        return None

    best: Optional[Dict[str, Any]] = None
    best_ts = -1
    best_needs_migration = False

    for path in _iter_registry_files():
        data = _load_registry_file(path)
        if not data:
            continue
        updated_at = _coerce_updated_at(data.get("updated_at"), path)
        if _is_stale(updated_at):
            continue

        effective, needs_migration = _effective_project_id(data)
        if effective != proj:
            continue

        if not _provider_pane_alive(data, prov):
            continue

        if updated_at > best_ts:
            best = data
            best_ts = updated_at
            best_needs_migration = needs_migration

    if best and best_needs_migration:
        _maybe_persist_project_id(best)

    return best


def load_registry_by_target(ccb_project_id: str, target: str) -> Optional[Dict[str, Any]]:
    proj = (ccb_project_id or "").strip()
    if not proj:
        return None

    try:
        canonical_target = validate_target(target)
    except ValueError:
        return None

    best: Optional[Dict[str, Any]] = None
    best_ts = -1
    best_needs_migration = False

    for path in _iter_registry_files():
        data = _load_registry_file(path)
        if not data:
            continue
        updated_at = _coerce_updated_at(data.get("updated_at"), path)
        if _is_stale(updated_at):
            continue

        effective, needs_migration = _effective_project_id(data)
        if effective != proj:
            continue

        if not _target_pane_alive(data, canonical_target):
            continue

        if updated_at > best_ts:
            best = data
            best_ts = updated_at
            best_needs_migration = needs_migration

    if best and best_needs_migration:
        _maybe_persist_project_id(best)

    return best


def upsert_registry(record: Dict[str, Any]) -> bool:
    session_id = record.get("ccb_session_id")
    if not session_id:
        _debug("Registry update skipped: missing ccb_session_id")
        return False
    path = registry_path_for_session(str(session_id))
    path.parent.mkdir(parents=True, exist_ok=True)

    data: Dict[str, Any] = {}
    if path.exists():
        existing = _load_registry_file(path)
        if isinstance(existing, dict):
            data.update(existing)

    providers = _get_providers_map(data)
    instances = _get_explicit_instances_map(data)

    incoming_providers = record.get("providers")
    if isinstance(incoming_providers, dict):
        for provider, entry in incoming_providers.items():
            if not isinstance(provider, str) or not isinstance(entry, dict):
                continue
            key = provider.strip().lower()
            providers.setdefault(key, {})
            _merge_entry(providers[key], entry)

            target = _default_target_for_provider(key)
            if target:
                instances.setdefault(target, {})
                _merge_entry(instances[target], entry)

    provider = record.get("provider")
    if isinstance(provider, str) and provider.strip():
        key = provider.strip().lower()
        scoped = _scoped_entry_from_record(record)
        if scoped:
            providers.setdefault(key, {})
            _merge_entry(providers[key], scoped)

            target = _default_target_for_provider(key)
            if target:
                instances.setdefault(target, {})
                _merge_entry(instances[target], scoped)

    for provider_name in ("codex", "gemini", "opencode", "claude"):
        legacy_entry = _provider_entry_from_legacy(record, provider_name)
        if not legacy_entry:
            continue
        providers.setdefault(provider_name, {})
        _merge_entry(providers[provider_name], legacy_entry)

        target = _default_target_for_provider(provider_name)
        if target:
            instances.setdefault(target, {})
            _merge_entry(instances[target], legacy_entry)

    incoming_instances = record.get("instances")
    if isinstance(incoming_instances, dict):
        for target, entry in incoming_instances.items():
            if not isinstance(target, str) or not isinstance(entry, dict):
                continue
            try:
                canonical_target = validate_target(target)
            except ValueError:
                _debug(f"Registry update skipped invalid target {target!r}")
                continue

            instances.setdefault(canonical_target, {})
            _merge_entry(instances[canonical_target], entry)

            provider_name = provider_of(canonical_target)
            providers.setdefault(provider_name, {})
            _merge_entry(providers[provider_name], entry)

    target = record.get("target")
    if isinstance(target, str) and target.strip():
        try:
            canonical_target = validate_target(target)
        except ValueError:
            _debug(f"Registry update skipped invalid target {target!r}")
        else:
            scoped = _scoped_entry_from_record(record)
            if scoped:
                instances.setdefault(canonical_target, {})
                _merge_entry(instances[canonical_target], scoped)

                provider_name = provider_of(canonical_target)
                providers.setdefault(provider_name, {})
                _merge_entry(providers[provider_name], scoped)

    for key, value in record.items():
        if value is None:
            continue
        if key in {"providers", "provider", "instances", "target"}:
            continue
        data[key] = value

    data["providers"] = providers
    if instances:
        data["instances"] = instances
    else:
        data.pop("instances", None)

    if not (data.get("ccb_project_id") or "").strip():
        wd = (data.get("work_dir") or "").strip()
        if wd:
            try:
                data["ccb_project_id"] = compute_ccb_project_id(Path(wd))
            except Exception:
                pass

    data["updated_at"] = int(time.time())

    try:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except Exception as exc:
        _debug(f"Failed to write registry {path}: {exc}")
        return False

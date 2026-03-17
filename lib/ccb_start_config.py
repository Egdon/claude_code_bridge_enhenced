from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional, Tuple

from session_utils import legacy_project_config_dir, project_config_dir
from target_id import validate_target


CONFIG_FILENAME = "ccb.config"
DEFAULT_PROVIDERS = ["codex", "opencode", "cursor"]
DEFAULT_TARGETS = ["codex@main", "opencode@main", "cursor@main"]


@dataclass
class StartConfig:
    data: dict
    path: Optional[Path] = None


_ALLOWED_PROVIDERS = {"codex", "gemini", "opencode", "claude", "droid", "cursor"}


def _parse_tokens(raw: str) -> list[str]:
    if not raw:
        return []
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line
        if "//" in stripped:
            stripped = stripped.split("//", 1)[0]
        if "#" in stripped:
            stripped = stripped.split("#", 1)[0]
        lines.append(stripped)
    cleaned = " ".join(lines)
    cleaned = re.sub(r"[\[\]\{\}\"']", " ", cleaned)
    parts = re.split(r"[,\s]+", cleaned)
    return [p for p in (part.strip() for part in parts) if p]



def _normalize_providers(tokens: list[str]) -> tuple[list[str], bool]:
    providers: list[str] = []
    seen: set[str] = set()
    cmd_enabled = False
    for raw in tokens:
        token = str(raw).strip().lower()
        if not token:
            continue
        if token == "cmd":
            cmd_enabled = True
            continue
        if token not in _ALLOWED_PROVIDERS:
            continue
        if token in seen:
            continue
        seen.add(token)
        providers.append(token)
    return providers, cmd_enabled



def _normalize_targets(tokens: list[str]) -> tuple[list[str], bool]:
    targets: list[str] = []
    seen: set[str] = set()
    cmd_enabled = False
    for raw in tokens:
        token = str(raw).strip()
        if not token:
            continue
        if token.lower() == "cmd":
            cmd_enabled = True
            continue
        try:
            canonical = validate_target(token)
        except ValueError:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        targets.append(canonical)
    return targets, cmd_enabled



def _providers_from_targets(targets: list[str]) -> list[str]:
    providers: list[str] = []
    seen: set[str] = set()
    for target in targets:
        provider = str(target).split("@", 1)[0].strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        providers.append(provider)
    return providers



def _targets_from_providers(providers: list[str]) -> list[str]:
    return [f"{provider}@main" for provider in providers]



def _assign_targets(data: dict, targets: list[str], *, cmd_enabled: bool = False) -> dict:
    data["targets"] = targets
    data["providers"] = _providers_from_targets(targets)
    if cmd_enabled and "cmd" not in data:
        data["cmd"] = True
    return data



def _assign_providers(data: dict, providers: list[str], *, cmd_enabled: bool = False) -> dict:
    data["providers"] = providers
    data["targets"] = _targets_from_providers(providers)
    if cmd_enabled and "cmd" not in data:
        data["cmd"] = True
    return data



def _contains_target_tokens(tokens: list[str]) -> bool:
    for raw in tokens:
        token = str(raw).strip()
        if not token:
            continue
        if token.lower() == "cmd":
            continue
        if "@" in token:
            return True
    return False



def _parse_config_obj(obj: object) -> dict:
    if isinstance(obj, dict):
        data = dict(obj)

        raw_targets = data.get("targets")
        target_tokens: list[str] = []
        if isinstance(raw_targets, str):
            target_tokens = _parse_tokens(raw_targets)
        elif isinstance(raw_targets, list):
            target_tokens = [str(p) for p in raw_targets if p is not None]
        elif raw_targets is not None:
            target_tokens = [str(raw_targets)]
        if target_tokens:
            targets, cmd_enabled = _normalize_targets(target_tokens)
            return _assign_targets(data, targets, cmd_enabled=cmd_enabled)

        raw_providers = data.get("providers")
        provider_tokens: list[str] = []
        if isinstance(raw_providers, str):
            provider_tokens = _parse_tokens(raw_providers)
        elif isinstance(raw_providers, list):
            provider_tokens = [str(p) for p in raw_providers if p is not None]
        elif raw_providers is not None:
            provider_tokens = [str(raw_providers)]

        if provider_tokens:
            if _contains_target_tokens(provider_tokens):
                targets, cmd_enabled = _normalize_targets(provider_tokens)
                return _assign_targets(data, targets, cmd_enabled=cmd_enabled)

            providers, cmd_enabled = _normalize_providers(provider_tokens)
            return _assign_providers(data, providers, cmd_enabled=cmd_enabled)
        return data

    if isinstance(obj, list):
        tokens = [str(p) for p in obj if p is not None]
        if _contains_target_tokens(tokens):
            targets, cmd_enabled = _normalize_targets(tokens)
            return _assign_targets({}, targets, cmd_enabled=cmd_enabled)

        providers, cmd_enabled = _normalize_providers(tokens)
        return _assign_providers({}, providers, cmd_enabled=cmd_enabled)

    if isinstance(obj, str):
        tokens = _parse_tokens(obj)
        if _contains_target_tokens(tokens):
            targets, cmd_enabled = _normalize_targets(tokens)
            return _assign_targets({}, targets, cmd_enabled=cmd_enabled)

        providers, cmd_enabled = _normalize_providers(tokens)
        return _assign_providers({}, providers, cmd_enabled=cmd_enabled)

    return {}



def _read_config(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        obj = None
    if obj is None:
        tokens = _parse_tokens(raw)
        if _contains_target_tokens(tokens):
            targets, cmd_enabled = _normalize_targets(tokens)
            return _assign_targets({}, targets, cmd_enabled=cmd_enabled)

        providers, cmd_enabled = _normalize_providers(tokens)
        return _assign_providers({}, providers, cmd_enabled=cmd_enabled)
    return _parse_config_obj(obj)



def _config_paths(work_dir: Path) -> Tuple[Path, Path, Path]:
    primary = project_config_dir(work_dir) / CONFIG_FILENAME
    legacy = legacy_project_config_dir(work_dir) / CONFIG_FILENAME
    global_path = Path.home() / ".ccb" / CONFIG_FILENAME
    return primary, legacy, global_path



def load_start_config(work_dir: Path) -> StartConfig:
    primary, legacy, global_path = _config_paths(work_dir)
    if primary.exists():
        return StartConfig(data=_read_config(primary), path=primary)
    if legacy.exists():
        return StartConfig(data=_read_config(legacy), path=legacy)
    if global_path.exists():
        return StartConfig(data=_read_config(global_path), path=global_path)
    return StartConfig(data={}, path=None)



def ensure_default_start_config(work_dir: Path) -> Tuple[Optional[Path], bool]:
    primary, legacy, _global_path = _config_paths(work_dir)
    if primary.exists():
        return primary, False
    if legacy.exists():
        return legacy, False
    target = primary
    if not primary.parent.exists() and legacy.parent.is_dir():
        target = legacy
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = ",".join(DEFAULT_TARGETS) + "\n"
        target.write_text(payload, encoding="utf-8")
        return target, True
    except Exception:
        return None, False

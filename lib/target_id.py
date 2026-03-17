from __future__ import annotations

import re

_TARGET_RE = re.compile(
    r"^(?P<provider>[a-z][a-z0-9_-]*)@(?P<instance>[a-z0-9][a-z0-9._-]*)$",
    re.IGNORECASE,
)


def split_target(value: str) -> tuple[str, str]:
    """Split and canonicalize a target string.

    Canonical form is `provider@instance`, both lower-cased.
    Raises `ValueError` when the input is invalid.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("target is empty")

    match = _TARGET_RE.fullmatch(raw)
    if not match:
        raise ValueError(f"invalid target: {value!r}")

    provider = match.group("provider").strip().lower()
    instance = match.group("instance").strip().lower()
    return provider, instance



def validate_target(value: str) -> str:
    """Validate and return canonical target string."""
    provider, instance = split_target(value)
    return f"{provider}@{instance}"



def provider_of(value: str) -> str:
    """Return canonical provider name from a target string."""
    provider, _instance = split_target(value)
    return provider



def instance_of(value: str) -> str:
    """Return canonical instance name from a target string."""
    _provider, instance = split_target(value)
    return instance



def to_fs_safe_slug(value: str) -> str:
    """Return a filesystem-safe single-segment slug for a target.

    The slug is intended for lock files, cache files, and log file names.
    Directory layouts should prefer `provider/instance` rather than this slug.
    """
    provider, instance = split_target(value)
    return f"{provider}--{instance}"

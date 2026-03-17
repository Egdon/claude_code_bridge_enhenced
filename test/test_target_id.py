from __future__ import annotations

import pytest

from target_id import instance_of, provider_of, split_target, to_fs_safe_slug, validate_target


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("codex@1", "codex@1"),
        (" CoDeX@Main ", "codex@main"),
        ("claude@review.v2", "claude@review.v2"),
        ("opencode@fast-mode", "opencode@fast-mode"),
    ],
)
def test_validate_target_returns_canonical_target(raw: str, expected: str) -> None:
    assert validate_target(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "codex",
        "@1",
        "codex@",
        "codex@@1",
        "codex @1",
        "codex@ bad",
        "1codex@main",
    ],
)
def test_validate_target_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_target(raw)


def test_split_target_provider_and_instance_are_canonicalized() -> None:
    provider, instance = split_target(" ClAuDe@Main-1 ")
    assert provider == "claude"
    assert instance == "main-1"


def test_provider_and_instance_helpers() -> None:
    assert provider_of("codex@review") == "codex"
    assert instance_of("codex@review") == "review"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("codex@1", "codex--1"),
        ("CoDeX@Main", "codex--main"),
        ("claude@review.v2", "claude--review.v2"),
    ],
)
def test_to_fs_safe_slug(raw: str, expected: str) -> None:
    assert to_fs_safe_slug(raw) == expected

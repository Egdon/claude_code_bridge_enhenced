from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_autonew_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "bin" / "autonew"
    loader = SourceFileLoader("autonew_targets", str(script_path))
    spec = importlib.util.spec_from_loader("autonew_targets", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_autonew_target_sends_reset_only_to_exact_target(monkeypatch, tmp_path: Path, capsys) -> None:
    autonew = _load_autonew_module()
    monkeypatch.chdir(tmp_path)

    sent: list[tuple[str, str]] = []

    class _FakeBackend:
        def is_alive(self, pane_id: str) -> bool:
            return True

        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

    monkeypatch.setattr(autonew, "_resolve_target_entry", lambda work_dir, target: ({"terminal": "tmux"}, {"pane_id": "%2"}))
    monkeypatch.setattr(autonew, "get_backend_for_session", lambda _record: _FakeBackend())

    rc = autonew.main(["autonew", "codex@2"])

    assert rc == autonew.EXIT_OK
    assert sent == [("%2", "/new")]
    assert "codex@2" in capsys.readouterr().out


def test_autonew_bare_provider_rejected_when_ambiguous(monkeypatch, tmp_path: Path, capsys) -> None:
    autonew = _load_autonew_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(autonew, "_resolve_provider_targets", lambda work_dir, provider: ["codex@1", "codex@2"])

    rc = autonew.main(["autonew", "codex"])

    assert rc == autonew.EXIT_ERROR
    assert "codex@1" in capsys.readouterr().err or "ambiguous" in capsys.readouterr().err.lower()

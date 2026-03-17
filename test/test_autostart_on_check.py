from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BIN_DIR = SCRIPT_DIR.parent / "bin"
LIB_DIR = SCRIPT_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from project_id import compute_ccb_project_id


ASKD_BIN = BIN_DIR / "askd"
CCB_PING_BIN = BIN_DIR / "ccb-ping"
CCB_MOUNTED_BIN = BIN_DIR / "ccb-mounted"


def _load_mounted_module() -> object:
    loader = SourceFileLoader("ccb_mounted_autostart_check", str(CCB_MOUNTED_BIN))
    spec = importlib.util.spec_from_loader("ccb_mounted_autostart_check", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _write_gemini_session(project_dir: Path) -> Path:
    cfg_dir = project_dir / ".ccb"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    session_file = cfg_dir / ".gemini-session"
    payload = {
        "active": True,
        "work_dir": str(project_dir),
        "runtime_dir": str(project_dir),
        "session_id": "test-session",
        "pane_id": "%1",
        "terminal": "tmux",
    }
    session_file.write_text(json.dumps(payload, ensure_ascii=True) + "\n", encoding="utf-8")
    return session_file


def _write_target_session(project_dir: Path, target: str, pane_id: str) -> Path:
    provider, _sep, instance = target.partition("@")
    session_dir = project_dir / ".ccb" / "sessions" / provider
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"{instance}.json"
    payload = {
        "active": True,
        "target": target,
        "work_dir": str(project_dir),
        "runtime_dir": str(project_dir),
        "session_id": f"{provider}-{instance}-session",
        "pane_id": pane_id,
        "terminal": "tmux",
    }
    session_file.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return session_file


def _write_registry_record(home_dir: Path, project_dir: Path, session_id: str, instances: dict[str, dict[str, str]]) -> Path:
    registry_path = home_dir / ".ccb" / "run" / f"ccb-session-{session_id}.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ccb_session_id": session_id,
        "ccb_project_id": compute_ccb_project_id(project_dir),
        "work_dir": str(project_dir),
        "terminal": "tmux",
        "updated_at": int(time.time()),
        "instances": instances,
    }
    registry_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return registry_path


def _shutdown_askd(run_dir: Path) -> None:
    env = dict(os.environ)
    env["CCB_RUN_DIR"] = str(run_dir)
    subprocess.run([str(ASKD_BIN), "--shutdown"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write_fake_tmux(bin_dir: Path) -> Path:
    tmux_bin = bin_dir / "tmux"
    tmux_bin.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == \"display-message\" ]]; then\n"
        "  target=\"\"\n"
        "  format=\"\"\n"
        "  while [[ $# -gt 0 ]]; do\n"
        "    case \"$1\" in\n"
        "      -t) target=\"$2\"; shift 2 ;;\n"
        "      -p) shift ;;\n"
        "      *) format=\"$1\"; shift ;;\n"
        "    esac\n"
        "  done\n"
        "  if [[ \"$format\" == \"#{pane_dead}\" ]]; then\n"
        "    printf '0\\n'\n"
        "    exit 0\n"
        "  fi\n"
        "  if [[ \"$format\" == \"#{pane_id}\" ]]; then\n"
        "    printf '%s\\n' \"${target:-%1}\"\n"
        "    exit 0\n"
        "  fi\n"
        "  printf '0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"${1:-}\" == \"has-session\" ]]; then\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux_bin.chmod(0o755)
    return tmux_bin


def test_ccb_ping_autostart_uses_session_file_work_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    other_dir = tmp_path / "other"
    run_dir = tmp_path / "run"
    project_dir.mkdir()
    other_dir.mkdir()
    run_dir.mkdir()
    session_file = _write_gemini_session(project_dir)

    env = dict(os.environ)
    env["CCB_GASKD"] = "1"
    env["CCB_GASKD_AUTOSTART"] = "1"
    env["CCB_RUN_DIR"] = str(run_dir)

    try:
        subprocess.run(
            [str(CCB_PING_BIN), "gemini", "--session-file", str(session_file), "--autostart"],
            cwd=str(other_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        assert (run_dir / "askd.json").exists()
    finally:
        _shutdown_askd(run_dir)


def test_ccb_mounted_autostart_uses_target_path(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    other_dir = tmp_path / "other"
    run_dir = tmp_path / "run"
    fake_bin_dir = tmp_path / "fake-bin"
    project_dir.mkdir()
    other_dir.mkdir()
    run_dir.mkdir()
    fake_bin_dir.mkdir()
    _write_target_session(project_dir, "gemini@main", "%1")
    _write_fake_tmux(fake_bin_dir)

    env = dict(os.environ)
    env["CCB_GASKD"] = "1"
    env["CCB_GASKD_AUTOSTART"] = "1"
    env["CCB_RUN_DIR"] = str(run_dir)
    env["PATH"] = f"{fake_bin_dir}{os.pathsep}{env.get('PATH', '')}"

    try:
        result = subprocess.run(
            [str(CCB_MOUNTED_BIN), "--autostart", str(project_dir)],
            cwd=str(other_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert parsed.get("cwd") == str(project_dir)
        assert parsed.get("mounted") == ["gemini@main"]
        assert parsed.get("mounted_providers") == ["gemini"]
        assert (run_dir / "askd.json").exists()
    finally:
        _shutdown_askd(run_dir)


def test_ccb_mounted_lists_multi_instance_targets_from_sessions_and_registry(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    other_dir = tmp_path / "other"
    fake_bin_dir = tmp_path / "fake-bin"
    project_dir.mkdir()
    other_dir.mkdir()
    fake_bin_dir.mkdir()

    _write_target_session(project_dir, "codex@1", "%1")
    _write_target_session(project_dir, "claude@main", "%3")
    _write_registry_record(
        tmp_path,
        project_dir,
        "multi-targets",
        {
            "codex@2": {"pane_id": "%2"},
        },
    )
    _write_fake_tmux(fake_bin_dir)

    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["PATH"] = f"{fake_bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [str(CCB_MOUNTED_BIN), str(project_dir)],
        cwd=str(other_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed.get("cwd") == str(project_dir)
    assert parsed.get("mounted") == ["claude@main", "codex@1", "codex@2"]
    assert parsed.get("mounted_providers") == ["claude", "codex"]


def test_ccb_mounted_autostart_uses_distinct_target_ids_for_multi_instance(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    mounted = _load_mounted_module()
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    _write_target_session(project_dir, "codex@1", "%1")
    _write_target_session(project_dir, "codex@2", "%2")
    _write_target_session(project_dir, "claude@main", "%3")

    autostart_targets: list[str] = []

    monkeypatch.setattr(mounted, "wait_for_daemon_ready", lambda spec, timeout_s=0.0, state_file=None: False)
    monkeypatch.setattr(
        mounted,
        "maybe_start_daemon",
        lambda spec, work_dir, target=None: (autostart_targets.append(str(target)) or True),
    )
    monkeypatch.setattr(
        mounted.target_cmd_utils,
        "ping_target",
        lambda work_dir, target: (target != "claude@main", f"[OK] {target}"),
    )

    rc = mounted.main(["ccb-mounted", "--autostart", str(project_dir)])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed.get("cwd") == str(project_dir)
    assert parsed.get("mounted") == ["codex@1", "codex@2"]
    assert parsed.get("mounted_providers") == ["codex"]
    assert autostart_targets == ["claude@main", "codex@1"]

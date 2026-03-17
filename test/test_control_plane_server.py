from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from control_plane import load_control_plane, record_target_activation, record_target_removal
from control_plane_server import start_control_plane_server


def _request(host: str, port: int, payload: dict[str, object]) -> dict[str, object]:
    with socket.create_connection((host, port), timeout=1.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(65536)
            assert chunk
            data += chunk
    return json.loads(data.decode("utf-8").strip())


def _handler_for(work_dir: Path, seen: list[dict[str, object]]):
    def _handler(request: dict[str, object]) -> dict[str, object]:
        seen.append(dict(request))
        req_id = request.get("id")
        op = str(request.get("op") or "").strip().lower()
        params = request.get("params") if isinstance(request.get("params"), dict) else {}

        if op == "add":
            record_target_activation(
                work_dir,
                session_id="sess-1",
                runtime_root=work_dir / "runtime",
                project_id="proj-1",
                terminal="tmux",
                target=str(request.get("target") or ""),
                pane_id=str(params.get("pane_id") or "").strip() or None,
            )
        elif op == "rm":
            record_target_removal(work_dir, str(request.get("target") or ""))

        state = dict(load_control_plane(work_dir) or {})
        return {
            "type": "control.response",
            "v": 1,
            "id": req_id,
            "exit_code": 0,
            "message": "OK",
            "changed": op in {"add", "rm"},
            "state": state,
            "active_targets": list(state.get("active_targets") or []),
        }

    return _handler


def test_start_server_writes_runtime_state(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    handle = start_control_plane_server(tmp_path, token="secret-token", request_handler=_handler_for(tmp_path, seen))
    try:
        payload = load_control_plane(tmp_path)
        assert payload is not None
        assert payload["host"] == handle.host
        assert payload["port"] == handle.port
        assert payload["token"] == "secret-token"
        assert payload["status"] == "running"
        assert int(payload["server_pid"]) > 0
    finally:
        handle.close()


def test_ping_and_token_auth_follow_current_protocol(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    handle = start_control_plane_server(tmp_path, token="secret-token", request_handler=_handler_for(tmp_path, seen))
    try:
        bad = _request(
            handle.host,
            handle.port,
            {"type": "control.ping", "v": 1, "id": "bad-1", "token": "wrong"},
        )
        assert bad == {
            "type": "control.response",
            "v": 1,
            "id": "bad-1",
            "exit_code": 1,
            "message": "Unauthorized",
        }

        good = _request(
            handle.host,
            handle.port,
            {"type": "control.ping", "v": 1, "id": "ping-1", "token": "secret-token"},
        )
        assert good == {
            "type": "control.pong",
            "v": 1,
            "id": "ping-1",
            "exit_code": 0,
            "message": "OK",
            "work_dir": str(tmp_path.resolve()),
        }
        assert seen == []
    finally:
        handle.close()


def test_control_request_add_status_rm_uses_request_handler_and_updates_state(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    handle = start_control_plane_server(tmp_path, token="secret-token", request_handler=_handler_for(tmp_path, seen))
    try:
        add = _request(
            handle.host,
            handle.port,
            {
                "type": "control.request",
                "v": 1,
                "id": "req-add",
                "token": "secret-token",
                "params": {"action": "add", "target": "codex@2", "pane_id": "%12"},
            },
        )
        assert add["type"] == "control.response"
        assert add["id"] == "req-add"
        assert add["exit_code"] == 0
        assert add["changed"] is True
        assert add["active_targets"] == ["codex@2"]
        assert add["state"]["target_panes"] == {"codex@2": "%12"}

        status = _request(
            handle.host,
            handle.port,
            {
                "type": "control.request",
                "v": 1,
                "id": "req-status",
                "token": "secret-token",
                "params": {"action": "status"},
            },
        )
        assert status["exit_code"] == 0
        assert status["changed"] is False
        assert status["state"]["active_targets"] == ["codex@2"]

        rm = _request(
            handle.host,
            handle.port,
            {
                "type": "control.request",
                "v": 1,
                "id": "req-rm",
                "token": "secret-token",
                "params": {"action": "rm", "target": "codex@2"},
            },
        )
        assert rm["exit_code"] == 0
        assert rm["changed"] is True
        assert rm["active_targets"] == []

        assert seen[0]["op"] == "add"
        assert seen[0]["target"] == "codex@2"
        assert seen[1]["op"] == "status"
        assert seen[2]["op"] == "rm"
        assert seen[2]["target"] == "codex@2"

        payload = load_control_plane(tmp_path)
        assert payload is not None
        assert payload.get("active_targets") in (None, [])
        assert payload.get("target_panes") in (None, {})
        assert payload["status"] == "running"
    finally:
        handle.close()


def test_control_shutdown_marks_runtime_stopped_and_exits_thread(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    handle = start_control_plane_server(tmp_path, token="secret-token", request_handler=_handler_for(tmp_path, seen))

    reply = _request(
        handle.host,
        handle.port,
        {"type": "control.shutdown", "v": 1, "id": "shutdown-1", "token": "secret-token"},
    )
    assert reply == {
        "type": "control.response",
        "v": 1,
        "id": "shutdown-1",
        "exit_code": 0,
        "message": "OK",
    }

    deadline = time.time() + 2.0
    while time.time() < deadline:
        payload = load_control_plane(tmp_path)
        if isinstance(payload, dict) and payload.get("status") == "stopped":
            break
        time.sleep(0.05)

    payload = load_control_plane(tmp_path)
    assert payload is not None
    assert payload["status"] == "stopped"

    handle.thread.join(timeout=2.0)
    assert not handle.thread.is_alive()

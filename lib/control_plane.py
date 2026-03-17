from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any

from askd_runtime import normalize_connect_host, random_token
from process_lock import ProviderLock
from session_utils import project_config_dir, safe_write_session
from target_id import provider_of, validate_target


CONTROL_PLANE_FILENAME = "control-plane.json"
CONTROL_PLANE_SCHEMA_VERSION = 1
_CONTROL_PLANE_PROTOCOL = "control"
_CONTROL_PLANE_STATES = {"starting", "running", "stopping", "stopped"}


ControlPlanePayload = dict[str, Any]


def control_plane_path(work_dir: Path | str) -> Path:
    return project_config_dir(Path(work_dir)) / CONTROL_PLANE_FILENAME


def _canonical_targets(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values or []:
        raw = str(value or "").strip()
        if not raw:
            continue
        try:
            canonical = validate_target(raw)
        except ValueError:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    return out


def _normalize_target_panes(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    out: dict[str, str] = {}
    for raw_target, raw_pane in value.items():
        try:
            canonical_target = validate_target(str(raw_target or "").strip())
        except ValueError:
            continue
        pane_id = str(raw_pane or "").strip()
        if pane_id:
            out[canonical_target] = pane_id
    return out


def _normalize_control_plane_payload(payload: ControlPlanePayload | None) -> ControlPlanePayload:
    data = dict(payload or {})
    data["schema_version"] = CONTROL_PLANE_SCHEMA_VERSION
    data["updated_at"] = int(time.time())

    active_targets = _canonical_targets(data.get("active_targets"))
    if active_targets:
        data["active_targets"] = active_targets
    else:
        data.pop("active_targets", None)

    target_panes = _normalize_target_panes(data.get("target_panes"))
    if target_panes:
        data["target_panes"] = target_panes
    else:
        data.pop("target_panes", None)

    parent_target = str(data.get("parent_target") or "").strip()
    if parent_target:
        try:
            data["parent_target"] = validate_target(parent_target)
        except ValueError:
            data.pop("parent_target", None)
    else:
        data.pop("parent_target", None)

    parent_pane_id = str(data.get("parent_pane_id") or "").strip()
    if parent_pane_id:
        data["parent_pane_id"] = parent_pane_id
    else:
        data.pop("parent_pane_id", None)

    for key in ("session_id", "runtime_root", "project_id", "terminal", "host", "connect_host", "token", "started_at", "heartbeat_at", "last_error"):
        raw = str(data.get(key) or "").strip()
        if raw:
            data[key] = raw
        else:
            data.pop(key, None)

    raw_status = str(data.get("status") or "").strip().lower()
    if raw_status in _CONTROL_PLANE_STATES:
        data["status"] = raw_status
    else:
        data.pop("status", None)

    for key in ("port", "server_pid"):
        raw_value = data.get(key)
        try:
            parsed = int(raw_value)
        except Exception:
            data.pop(key, None)
            continue
        if parsed > 0:
            data[key] = parsed
        else:
            data.pop(key, None)

    return data


def load_control_plane(work_dir: Path | str) -> ControlPlanePayload | None:
    path = control_plane_path(work_dir)
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return _normalize_control_plane_payload(data)


def write_control_plane(work_dir: Path | str, payload: ControlPlanePayload) -> Path:
    path = control_plane_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _normalize_control_plane_payload(payload)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    ok, err = safe_write_session(path, content)
    if not ok:
        raise OSError(err or f"failed to write {path}")
    return path


def load_control_plane_context(work_dir: Path | str) -> dict[str, object] | None:
    data = load_control_plane(work_dir)
    if not isinstance(data, dict):
        return None

    session_id = str(data.get("session_id") or "").strip()
    runtime_root = str(data.get("runtime_root") or "").strip()
    if not session_id or not runtime_root:
        return None

    terminal = str(data.get("terminal") or "").strip() or None
    parent_pane = str(data.get("parent_pane_id") or "").strip() or None
    return {
        "session_id": session_id,
        "runtime_root": Path(runtime_root),
        "parent_pane": parent_pane,
        "terminal": terminal,
    }


def control_plane_endpoint(work_dir: Path | str) -> dict[str, object] | None:
    data = load_control_plane(work_dir)
    if not isinstance(data, dict):
        return None

    endpoint_source = data
    nested = data.get("control_server")
    if isinstance(nested, dict):
        endpoint_source = dict(nested)
        if "connect_host" not in endpoint_source and endpoint_source.get("host"):
            endpoint_source["connect_host"] = normalize_connect_host(str(endpoint_source.get("host") or ""))
        if "server_pid" not in endpoint_source and endpoint_source.get("pid"):
            endpoint_source["server_pid"] = endpoint_source.get("pid")

    try:
        host = str(endpoint_source.get("connect_host") or endpoint_source.get("host") or "").strip()
        port = int(endpoint_source.get("port") or 0)
        token = str(endpoint_source.get("token") or "").strip()
    except Exception:
        return None
    if str(endpoint_source.get("status") or "").strip().lower() != "running":
        return None
    if not host or port <= 0 or not token:
        return None
    return {
        "host": host,
        "port": port,
        "token": token,
        "status": str(endpoint_source.get("status") or "").strip().lower(),
        "server_pid": int(endpoint_source.get("server_pid") or 0) or None,
    }


def _control_plane_lock(work_dir: Path | str) -> ProviderLock:
    lock_scope = str(Path(work_dir).expanduser().resolve())
    return ProviderLock("control-plane", timeout=5.0, cwd=lock_scope)


def _mutate_control_plane(work_dir: Path | str, mutator) -> Path | None:
    # Serialize read-modify-write cycles across concurrent `ccb add/rm` calls.
    # TODO: once live control socket lands, move all mutations behind the single control-plane owner.
    with _control_plane_lock(work_dir):
        current = load_control_plane(work_dir)
        updated = mutator(dict(current or {}))
        if updated is None:
            return None
        return write_control_plane(work_dir, updated)


def record_control_plane_runtime(
    work_dir: Path | str,
    *,
    status: str,
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
    server_pid: int | None = None,
    last_error: str | None = None,
) -> Path:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _CONTROL_PLANE_STATES:
        raise ValueError(f"invalid control-plane status: {status!r}")

    def _apply(data: ControlPlanePayload) -> ControlPlanePayload:
        data["status"] = normalized_status
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        data["heartbeat_at"] = now_str
        if normalized_status == "running" and "started_at" not in data:
            data["started_at"] = now_str

        normalized_host = str(host or "").strip()
        if normalized_host:
            data["host"] = normalized_host
            data["connect_host"] = normalize_connect_host(normalized_host)
        elif normalized_status != "running":
            data.pop("host", None)
            data.pop("connect_host", None)

        if port is not None and int(port) > 0:
            data["port"] = int(port)
        elif normalized_status != "running":
            data.pop("port", None)

        token_value = str(token or "").strip()
        if token_value:
            data["token"] = token_value
        elif normalized_status != "running":
            data.pop("token", None)

        if server_pid is not None and int(server_pid) > 0:
            data["server_pid"] = int(server_pid)
        elif normalized_status != "running":
            data.pop("server_pid", None)

        error_value = str(last_error or "").strip()
        if error_value:
            data["last_error"] = error_value
        elif normalized_status == "running":
            data.pop("last_error", None)
        return data

    path = _mutate_control_plane(work_dir, _apply)
    assert path is not None
    return path


def _control_plane_roundtrip(work_dir: Path | str, request: dict, timeout_s: float) -> dict | None:
    endpoint = control_plane_endpoint(work_dir)
    if not isinstance(endpoint, dict):
        return None
    host = str(endpoint["host"])
    port = int(endpoint["port"])
    token = str(endpoint["token"])
    req = dict(request)
    req.setdefault("type", f"{_CONTROL_PLANE_PROTOCOL}.request")
    req.setdefault("v", 1)
    req.setdefault("id", f"{int(time.time() * 1000)}")
    req["token"] = token
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = b""
            deadline = time.time() + timeout_s
            while b"\n" not in buf and time.time() < deadline:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        if b"\n" not in buf:
            return None
        line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        payload = json.loads(line)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def ping_control_plane(work_dir: Path | str, timeout_s: float = 0.5) -> bool:
    response = _control_plane_roundtrip(
        work_dir,
        {"type": f"{_CONTROL_PLANE_PROTOCOL}.ping", "id": "ping"},
        timeout_s,
    )
    if not isinstance(response, dict):
        return False
    return response.get("type") in {f"{_CONTROL_PLANE_PROTOCOL}.pong", f"{_CONTROL_PLANE_PROTOCOL}.response"} and int(response.get("exit_code") or 0) == 0


def request_control_plane_operation(
    work_dir: Path | str,
    *,
    op: str,
    target: str | None = None,
    timeout_s: float = 2.0,
) -> dict | None:
    payload: dict[str, object] = {"op": str(op or "").strip().lower()}
    if target is not None:
        payload["target"] = validate_target(target)
    response = _control_plane_roundtrip(work_dir, payload, timeout_s)
    return response if isinstance(response, dict) else None


def shutdown_control_plane(work_dir: Path | str, timeout_s: float = 1.0) -> bool:
    response = _control_plane_roundtrip(
        work_dir,
        {"type": f"{_CONTROL_PLANE_PROTOCOL}.shutdown", "id": "shutdown"},
        timeout_s,
    )
    return isinstance(response, dict) and int(response.get("exit_code") or 0) == 0


def next_control_plane_token() -> str:
    return random_token()


def record_target_activation(
    work_dir: Path | str,
    *,
    session_id: str,
    runtime_root: Path | str,
    project_id: str,
    terminal: str | None,
    target: str,
    pane_id: str | None = None,
) -> Path:
    canonical_target = validate_target(target)

    def _apply(data: ControlPlanePayload) -> ControlPlanePayload:
        active_targets = list(data.get("active_targets") or [])
        active_targets.append(canonical_target)
        data["active_targets"] = _canonical_targets(active_targets)
        data["session_id"] = str(session_id)
        data["runtime_root"] = str(Path(runtime_root))
        data["project_id"] = str(project_id)
        if terminal:
            data["terminal"] = str(terminal)

        target_panes = _normalize_target_panes(data.get("target_panes"))
        pane_value = str(pane_id or "").strip()
        if pane_value:
            target_panes[canonical_target] = pane_value
            data["target_panes"] = target_panes
            data["parent_target"] = canonical_target
            data["parent_pane_id"] = pane_value
        elif target_panes:
            data["target_panes"] = target_panes
        return data

    path = _mutate_control_plane(work_dir, _apply)
    assert path is not None
    return path


def record_target_removal(work_dir: Path | str, target: str) -> Path | None:
    canonical_target = validate_target(target)

    def _apply(data: ControlPlanePayload) -> ControlPlanePayload | None:
        if not data:
            return None

        data["active_targets"] = [item for item in _canonical_targets(data.get("active_targets")) if item != canonical_target]

        target_panes = _normalize_target_panes(data.get("target_panes"))
        removed_pane = target_panes.pop(canonical_target, None)
        if target_panes:
            data["target_panes"] = target_panes
        else:
            data.pop("target_panes", None)

        parent_target = str(data.get("parent_target") or "").strip()
        parent_pane_id = str(data.get("parent_pane_id") or "").strip()
        parent_invalid = False
        if parent_target:
            try:
                parent_invalid = validate_target(parent_target) == canonical_target
            except ValueError:
                parent_invalid = True
        if removed_pane and parent_pane_id and parent_pane_id == removed_pane:
            parent_invalid = True

        if parent_invalid:
            replacement_target = None
            replacement_pane = None
            active_targets = list(data.get("active_targets") or [])
            same_provider_targets = [
                candidate for candidate in active_targets if provider_of(candidate) == provider_of(canonical_target)
            ]
            for candidates in (same_provider_targets, active_targets):
                for candidate in reversed(candidates):
                    pane_id = target_panes.get(candidate)
                    if pane_id:
                        replacement_target = candidate
                        replacement_pane = pane_id
                        break
                if replacement_target and replacement_pane:
                    break
            if replacement_target and replacement_pane:
                data["parent_target"] = replacement_target
                data["parent_pane_id"] = replacement_pane
            else:
                data.pop("parent_target", None)
                data.pop("parent_pane_id", None)

        return data

    return _mutate_control_plane(work_dir, _apply)

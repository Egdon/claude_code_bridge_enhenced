from __future__ import annotations

import json
import os
import socketserver
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from control_plane import record_control_plane_runtime


LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}
_CONTROL_PROTOCOL = "control"

RequestHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ControlPlaneServerHandle:
    work_dir: Path
    host: str
    port: int
    token: str
    server: socketserver.ThreadingTCPServer
    thread: threading.Thread

    def close(self, timeout: float = 2.0) -> None:
        try:
            self.server.request_shutdown()
        except Exception:
            pass
        try:
            self.thread.join(timeout=timeout)
        except Exception:
            pass


class _ControlPlaneTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        work_dir: Path,
        token: str,
        request_handler: RequestHandler,
    ):
        self.work_dir = Path(work_dir).expanduser().resolve()
        self.token = str(token or "").strip()
        self.request_handler = request_handler
        self._shutdown_started = False
        self._state_lock = threading.Lock()
        super().__init__(server_address, _ControlPlaneRequestHandler)
        self._persist_state("running")

    def _persist_state(self, status: str, *, last_error: str | None = None) -> None:
        with self._state_lock:
            kwargs: dict[str, Any] = {"status": status, "last_error": last_error}
            if status == "running":
                kwargs.update(
                    {
                        "host": str(self.server_address[0]),
                        "port": int(self.server_address[1]),
                        "token": self.token,
                        "server_pid": os.getpid(),
                    }
                )
            record_control_plane_runtime(self.work_dir, **kwargs)

    def request_shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._persist_state("stopping")

        def _do_shutdown() -> None:
            try:
                self.shutdown()
            finally:
                try:
                    self.server_close()
                finally:
                    try:
                        self._persist_state("stopped")
                    except Exception:
                        pass

        threading.Thread(target=_do_shutdown, daemon=True, name="control-plane-shutdown").start()


class _ControlPlaneRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        request = self._read_request()
        if request is None:
            return
        response = self._dispatch(request)
        self._write_response(response)

    def _read_request(self) -> dict[str, Any] | None:
        try:
            raw = self.rfile.readline(1024 * 1024)
        except Exception:
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return {"_decode_error": True}
        return payload if isinstance(payload, dict) else {"_decode_error": True}

    def _write_response(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False) + "\n"
        self.wfile.write(body.encode("utf-8"))
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _response(self, request: dict[str, Any], *, exit_code: int, message: str) -> dict[str, Any]:
        return {
            "type": f"{_CONTROL_PROTOCOL}.response",
            "v": 1,
            "id": request.get("id"),
            "exit_code": int(exit_code),
            "message": str(message),
        }

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("_decode_error"):
            return self._response(request, exit_code=1, message="request must be a JSON object")

        token = str(request.get("token") or "").strip()
        if token != self.server.token:
            return self._response(request, exit_code=1, message="Unauthorized")

        req_type = str(request.get("type") or "").strip()
        legacy_method = str(request.get("method") or "").strip()
        effective_type = req_type or legacy_method

        if effective_type == f"{_CONTROL_PROTOCOL}.ping":
            return {
                "type": f"{_CONTROL_PROTOCOL}.pong",
                "v": 1,
                "id": request.get("id"),
                "exit_code": 0,
                "message": "OK",
                "work_dir": str(self.server.work_dir),
            }

        if effective_type == f"{_CONTROL_PROTOCOL}.shutdown":
            self.server.request_shutdown()
            return self._response(request, exit_code=0, message="OK")

        if effective_type != f"{_CONTROL_PROTOCOL}.request":
            return self._response(request, exit_code=1, message=f"unsupported control message: {effective_type or '<empty>'}")

        normalized = dict(request)
        params = request.get("params")
        if isinstance(params, dict):
            action = str(params.get("action") or normalized.get("op") or "").strip().lower()
            normalized["op"] = action
            if "target" not in normalized and params.get("target") is not None:
                normalized["target"] = params.get("target")

        try:
            response = self.server.request_handler(normalized)
        except Exception as exc:
            try:
                self.server._persist_state("running", last_error=str(exc))
            except Exception:
                pass
            return self._response(request, exit_code=1, message=f"Internal control-plane error: {exc}")

        if not isinstance(response, dict):
            return self._response(request, exit_code=1, message="control handler returned invalid response")
        return response


def start_control_plane_server(
    work_dir: Path | str,
    *,
    token: str,
    request_handler: RequestHandler,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ControlPlaneServerHandle:
    normalized_host = str(host or "127.0.0.1").strip() or "127.0.0.1"
    if normalized_host not in LOCALHOST_HOSTS:
        raise ValueError(f"control server must bind to localhost: {host!r}")
    token_value = str(token or "").strip()
    if not token_value:
        raise ValueError("control server token must not be empty")

    resolved_work_dir = Path(work_dir).expanduser().resolve()
    server = _ControlPlaneTCPServer(
        (normalized_host, int(port)),
        work_dir=resolved_work_dir,
        token=token_value,
        request_handler=request_handler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="control-plane-server")
    thread.start()
    actual_host, actual_port = server.server_address[:2]
    return ControlPlaneServerHandle(
        work_dir=resolved_work_dir,
        host=str(actual_host),
        port=int(actual_port),
        token=server.token,
        server=server,
        thread=thread,
    )

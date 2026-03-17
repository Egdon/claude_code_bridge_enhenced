from __future__ import annotations

import importlib
import importlib.util
import threading
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

from askd.adapters.base import ProviderResult
from askd.daemon import UnifiedAskDaemon
from askd.registry import ProviderRegistry
from session_store import write_target_session


PROVIDER_SESSION_MODULES = [
    ("caskd_session", "codex", "codex@1", "codex@2"),
    ("gaskd_session", "gemini", "gemini@1", "gemini@2"),
    ("oaskd_session", "opencode", "opencode@1", "opencode@2"),
    ("daskd_session", "droid", "droid@1", "droid@2"),
    ("uaskd_session", "cursor", "cursor@1", "cursor@2"),
    ("laskd_session", "claude", "claude@1", "claude@2"),
]


def _load_bin_module(name: str, path: Path) -> object:
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_cask() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    return _load_bin_module("cask_target_routing", repo_root / "bin" / "cask")


def test_provider_session_loader_and_session_key_are_target_scoped(tmp_path: Path) -> None:
    for module_name, provider, target_a, target_b in PROVIDER_SESSION_MODULES:
        mod = importlib.import_module(module_name)
        payload_a = {
            "target": target_a,
            "ccb_project_id": "proj-1",
            "work_dir": str(tmp_path),
            "terminal": "tmux",
            "pane_id": "%11",
            "active": True,
        }
        payload_b = {
            "target": target_b,
            "ccb_project_id": "proj-1",
            "work_dir": str(tmp_path),
            "terminal": "tmux",
            "pane_id": "%12",
            "active": True,
        }
        write_target_session(tmp_path, target_a, payload_a)
        write_target_session(tmp_path, target_b, payload_b)

        session_a = mod.load_project_session(tmp_path, target_a)
        session_b = mod.load_project_session(tmp_path, target_b)

        assert session_a is not None, module_name
        assert session_b is not None, module_name
        assert session_a.data["target"] == target_a, module_name
        assert session_b.data["target"] == target_b, module_name
        assert mod.compute_session_key(session_a, target_a) != mod.compute_session_key(session_b, target_b), module_name
        assert mod.compute_session_key(session_a, target_a).startswith(f"{provider}:proj-1:"), module_name


class _FakeAdapter:
    key = "codex"
    spec = SimpleNamespace()
    session_filename = ".codex-session"

    def on_start(self) -> None:
        return None

    def on_stop(self) -> None:
        return None


class _RoutingAdapter(_FakeAdapter):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.started_targets: list[str] = []
        self.finished_targets: list[str] = []
        self.parallel_ready = threading.Event()
        self.parallel_release = threading.Event()
        self.first_task_entered = threading.Event()
        self.allow_first_finish = threading.Event()
        self.second_task_entered = threading.Event()

    def load_session(self, work_dir: Path, target: str | None = None):
        return SimpleNamespace(data={"target": target or "codex@main"})

    def compute_session_key(self, session, target: str | None = None) -> str:
        return f"codex:proj-1:{target or 'main'}"

    def handle_task(self, task) -> ProviderResult:
        target = str(task.request.target or "codex@main")
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started_targets.append(target)
            start_index = len(self.started_targets)
            if self.active >= 2:
                self.parallel_ready.set()

        if start_index == 1:
            self.first_task_entered.set()
            self.parallel_ready.wait(timeout=0.4)
            self.allow_first_finish.wait(timeout=0.4)
        else:
            self.second_task_entered.set()
            self.parallel_release.set()

        with self._lock:
            self.finished_targets.append(target)
            self.active -= 1

        return ProviderResult(
            exit_code=0,
            reply=f"ok:{target}",
            req_id=task.req_id,
            session_key=self.compute_session_key(None, target),
            done_seen=True,
        )


def _run_request_in_thread(
    daemon: UnifiedAskDaemon,
    *,
    client_id: str,
    target: str,
    responses: dict[str, dict],
) -> threading.Thread:
    def _runner() -> None:
        responses[client_id] = daemon._handle_request(
            {
                "type": "ask.request",
                "v": 1,
                "id": client_id,
                "provider": "codex",
                "target": target,
                "instance": target.split("@", 1)[1],
                "caller": "claude",
                "caller_target": "claude@main",
                "work_dir": "/tmp/project",
                "timeout_s": 1,
                "message": f"hello {target}",
            }
        )

    thread = threading.Thread(target=_runner)
    thread.start()
    return thread


def test_unified_askd_request_carries_target_fields(monkeypatch) -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAdapter())
    daemon = UnifiedAskDaemon(registry=registry)

    captured: dict[str, object] = {}

    done_event = threading.Event()
    done_event.set()
    task = SimpleNamespace(
        done_event=done_event,
        result=ProviderResult(
            exit_code=0,
            reply="ok",
            req_id="req-1",
            session_key="codex:proj-1:2",
            done_seen=True,
        ),
    )

    def _fake_submit(provider_key: str, request) -> object:
        captured["provider_key"] = provider_key
        captured["request"] = request
        return task

    monkeypatch.setattr(daemon.pool, "submit", _fake_submit)

    response = daemon._handle_request(
        {
            "type": "ask.request",
            "v": 1,
            "id": "client-1",
            "provider": "codex",
            "target": "codex@2",
            "instance": "2",
            "caller": "claude",
            "caller_target": "claude@main",
            "work_dir": "/tmp/project",
            "timeout_s": 3,
            "message": "hello",
        }
    )

    request = captured["request"]
    assert response["exit_code"] == 0
    assert captured["provider_key"] == "codex"
    assert request.provider == "codex"
    assert request.target == "codex@2"
    assert request.instance == "2"
    assert request.caller_target == "claude@main"



def test_cask_forwards_target_to_workdir_resolution_and_daemon(monkeypatch, tmp_path: Path) -> None:
    cask = _load_cask()
    monkeypatch.chdir(tmp_path)

    captured: dict[str, object] = {}

    def _fake_resolve_work_dir_with_registry(spec, **kwargs):
        captured["resolve_kwargs"] = kwargs
        return tmp_path, None

    def _fake_try_daemon_request(spec, work_dir, message, timeout, quiet, state_file, output_path=None, target=None, caller_target=None):
        captured["request"] = {
            "work_dir": work_dir,
            "message": message,
            "target": target,
            "caller_target": caller_target,
        }
        return ("ok", 0)

    monkeypatch.setattr(cask, "resolve_work_dir_with_registry", _fake_resolve_work_dir_with_registry)
    monkeypatch.setattr(cask, "try_daemon_request", _fake_try_daemon_request)
    monkeypatch.setattr(cask, "maybe_start_daemon", lambda *_args, **_kwargs: False)

    rc = cask.main(["cask", "--target", "codex@2", "hello target"])

    assert rc == 0
    assert captured["resolve_kwargs"]["target"] == "codex@2"
    assert captured["request"]["target"] == "codex@2"


def test_unified_worker_pool_allows_parallel_requests_for_different_targets() -> None:
    registry = ProviderRegistry()
    adapter = _RoutingAdapter()
    registry.register(adapter)
    daemon = UnifiedAskDaemon(registry=registry)

    responses: dict[str, dict] = {}
    thread_a = _run_request_in_thread(daemon, client_id="client-1", target="codex@1", responses=responses)
    thread_b = _run_request_in_thread(daemon, client_id="client-2", target="codex@2", responses=responses)

    assert adapter.parallel_ready.wait(timeout=0.5), "different targets should be able to run concurrently"
    adapter.allow_first_finish.set()
    assert adapter.parallel_release.wait(timeout=0.5), "second target should be able to enter before first finishes"

    thread_a.join(timeout=1)
    thread_b.join(timeout=1)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert adapter.max_active >= 2
    assert responses["client-1"]["exit_code"] == 0
    assert responses["client-2"]["exit_code"] == 0
    assert set(adapter.started_targets[:2]) == {"codex@1", "codex@2"}



def test_unified_worker_pool_serializes_requests_for_same_target() -> None:
    registry = ProviderRegistry()
    adapter = _RoutingAdapter()
    registry.register(adapter)
    daemon = UnifiedAskDaemon(registry=registry)

    responses: dict[str, dict] = {}
    thread_a = _run_request_in_thread(daemon, client_id="client-1", target="codex@1", responses=responses)

    assert adapter.first_task_entered.wait(timeout=0.5), "first same-target request should start"

    thread_b = _run_request_in_thread(daemon, client_id="client-2", target="codex@1", responses=responses)
    time.sleep(0.1)

    assert adapter.max_active == 1
    assert adapter.second_task_entered.is_set() is False
    assert adapter.started_targets == ["codex@1"]

    adapter.allow_first_finish.set()
    assert adapter.second_task_entered.wait(timeout=0.5), "second same-target request should start only after first completes"

    thread_a.join(timeout=1)
    thread_b.join(timeout=1)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert adapter.max_active == 1
    assert adapter.started_targets == ["codex@1", "codex@1"]
    assert adapter.finished_targets == ["codex@1", "codex@1"]
    assert responses["client-1"]["exit_code"] == 0
    assert responses["client-2"]["exit_code"] == 0

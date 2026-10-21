import threading
from collections.abc import Callable
from concurrent.futures import Future

import msgspec

from .collections import CollectionCache
from .events import emit
from .messages import GatherFactsParams, HostSpec, ResultFrame, RunParams

RunAction = Callable[[RunParams], ResultFrame]

# Fact gathering + caching, server-owned (design §4.7). Lives in the ansible-free
# parent: it imports no ansible, it just orchestrates `setup` runs through whatever
# `run_action` it is given (the fork pool) and caches the result.


def _normalize(subset: list[str]) -> tuple[str, ...]:
    return tuple(sorted(subset))


class FactCache:
    """Per-host fact cache, keyed by (host.id, normalized gather_subset). Single-flight:
    concurrent callers for the same key share one gather (Future-per-key); a failed
    gather is evicted so the next call retries rather than caching the error."""

    def __init__(self, run_action: RunAction):
        self._run = run_action
        self._futures: dict[tuple[str | None, tuple[str, ...]], Future] = {}
        self._lock = threading.Lock()

    def ensure(self, host: HostSpec, task_vars: dict) -> dict:
        subset = _normalize(host.gather_subset or ["min"])
        key = (host.id, subset)
        with self._lock:
            fut = self._futures.get(key)
            owner = fut is None
            if owner:
                fut = Future()
                self._futures[key] = fut
        if owner:
            try:
                fut.set_result(self._gather(host, task_vars, subset))
            except BaseException as exc:  # noqa: BLE001 — propagate after eviction
                with self._lock:
                    if self._futures.get(key) is fut:
                        del self._futures[key]
                fut.set_exception(exc)
        return fut.result()

    def invalidate(self, host_id: str | None) -> None:
        with self._lock:
            for key in [k for k in self._futures if k[0] == host_id]:
                del self._futures[key]

    def _gather(self, host: HostSpec, task_vars: dict, subset: tuple[str, ...]) -> dict:
        token = "facts:%s:%s" % (host.id, ",".join(subset))
        emit("progress", {"token": token, "phase": "begin", "kind": "facts",
                          "host": host.id, "subset": list(subset)})
        try:
            frame = self._run(RunParams(
                host=HostSpec(connection=host.connection, become=host.become),
                fqcn="ansible.builtin.setup",
                args={"gather_subset": list(subset)},
                vars=task_vars,
            ))
        except BaseException:
            emit("progress", {"token": token, "phase": "end", "kind": "facts", "ok": False})
            raise
        if frame.status != "ok":
            emit("progress", {"token": token, "phase": "end", "kind": "facts", "ok": False})
            raise RuntimeError("fact gather failed: %r" % (frame.result,))
        emit("progress", {"token": token, "phase": "end", "kind": "facts", "ok": True})
        return frame.result["ansible_facts"]


def _discovered_interpreter(frame: ResultFrame) -> str | None:
    facts = frame.result.get("ansible_facts") if isinstance(frame.result, dict) else None
    value = facts.get("discovered_interpreter_python") if isinstance(facts, dict) else None
    return value if isinstance(value, str) else None


def make_handlers(run_action: RunAction, collections_path: str) -> dict[str, tuple[type, Callable]]:
    """Build the JSON-RPC method handlers that wrap execution with collection + fact
    provisioning. `run_action(params: RunParams) -> ResultFrame` is the pool's executor."""
    facts = FactCache(run_action)
    collections = CollectionCache(collections_path)
    # Per-host interpreter cache: ansible discovers the interpreter on a cold task and
    # returns it as discovered_interpreter_python; every task is a fresh worker with no
    # memory, so we cache it parent-side and reinject it to avoid re-discovery each task.
    interpreters: dict[str, str] = {}

    def run(params: RunParams) -> ResultFrame:
        if params.collection is not None:
            collections.ensure(params.collection)   # install before the worker runs
        host = params.host
        vars = params.vars
        if host.gather_subset is not None:
            gathered = facts.ensure(host, vars)
            # ansible exposes facts both namespaced and flattened as top-level vars
            vars = {**vars, "ansible_facts": gathered, **gathered}
        if ("ansible_python_interpreter" not in vars
                and host.id is not None and host.id in interpreters):
            vars = {**vars, "ansible_python_interpreter": interpreters[host.id]}
        if vars is not params.vars:
            params = msgspec.structs.replace(params, vars=vars)
        frame = run_action(params)
        if host.id is not None:
            discovered = _discovered_interpreter(frame)
            if discovered:
                interpreters[host.id] = discovered
        return frame

    def gather(params: GatherFactsParams) -> dict:
        return facts.ensure(params.host, params.vars)

    return {"runAction": (RunParams, run), "gatherFacts": (GatherFactsParams, gather)}

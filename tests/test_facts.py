import sys
import threading
import time

import msgspec
import pytest

from ansible_jsonrpc_server.facts import FactCache, make_handlers
from ansible_jsonrpc_server.messages import (
    Connection, GatherFactsParams, HostSpec, ResultFrame, RunParams,
)

HOST = HostSpec(id="h1", connection=Connection(type="local"), gather_subset=["min"])


def _setup_frame(facts):
    return ResultFrame(status="ok", changed=False, result={"ansible_facts": facts})


def test_single_flight_one_gather_for_concurrent_same_host():
    calls = []

    def run(params):
        calls.append(params.fqcn)
        time.sleep(0.1)  # hold the slot so the other callers pile onto one Future
        return _setup_frame({"ansible_system": "Linux"})

    cache = FactCache(run)
    results = []
    start = threading.Barrier(5)

    def worker():
        start.wait()
        results.append(cache.ensure(HOST, {}))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls == ["ansible.builtin.setup"]          # one gather, not five
    assert all(r["ansible_system"] == "Linux" for r in results)


def test_cache_hit_does_not_regather():
    calls = []

    def run(params):
        calls.append(1)
        return _setup_frame({"ansible_system": "Linux"})

    cache = FactCache(run)
    cache.ensure(HOST, {})
    cache.ensure(HOST, {})
    assert len(calls) == 1


def test_failure_is_evicted_so_next_call_retries():
    state = {"n": 0}

    def run(params):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return _setup_frame({"ansible_system": "Linux"})

    cache = FactCache(run)
    with pytest.raises(RuntimeError):
        cache.ensure(HOST, {})
    facts = cache.ensure(HOST, {})         # retried, not a cached failure
    assert facts["ansible_system"] == "Linux"
    assert state["n"] == 2


def test_distinct_subsets_partition_the_cache():
    calls = []

    def run(params):
        calls.append(tuple(params.args["gather_subset"]))
        return _setup_frame({"ansible_system": "Linux"})

    cache = FactCache(run)
    cache.ensure(msgspec.structs.replace(HOST, gather_subset=["min"]), {})
    cache.ensure(msgspec.structs.replace(HOST, gather_subset=["all"]), {})
    cache.ensure(msgspec.structs.replace(HOST, gather_subset=["min"]), {})   # hits
    assert len(calls) == 2


def test_run_action_injects_facts_when_subset_present():
    captured = {}

    def run(params):
        if params.fqcn == "ansible.builtin.setup":
            return _setup_frame({"ansible_pkg_mgr": "apt"})
        captured["vars"] = params.vars
        return ResultFrame(status="ok", changed=False, result={})

    _, handler = make_handlers(run, ".")["runAction"]
    handler(RunParams(host=HOST, fqcn="ansible.builtin.ping", vars={"x": 1}))
    assert captured["vars"]["ansible_pkg_mgr"] == "apt"              # flattened top-level
    assert captured["vars"]["ansible_facts"]["ansible_pkg_mgr"] == "apt"  # and namespaced
    assert captured["vars"]["x"] == 1                                # original vars preserved


def test_run_action_without_subset_does_not_gather():
    calls = []

    def run(params):
        calls.append(params.fqcn)
        return ResultFrame(status="ok", changed=False, result={})

    _, handler = make_handlers(run, ".")["runAction"]
    host = HostSpec(id="h1", connection=Connection(type="local"))  # no gather_subset
    handler(RunParams(host=host, fqcn="ansible.builtin.ping"))
    assert calls == ["ansible.builtin.ping"]            # no setup call


def test_gather_facts_handler_returns_facts():
    _, handler = make_handlers(lambda p: _setup_frame({"ansible_pkg_mgr": "apt"}), ".")["gatherFacts"]
    assert handler(GatherFactsParams(host=HOST)) == {"ansible_pkg_mgr": "apt"}


def test_real_fact_gather_through_pool():
    from ansible_jsonrpc_server.pool import TaskPool

    pool = TaskPool(forks=2)
    try:
        cache = FactCache(pool.run_action)
        facts = cache.ensure(HOST, {"ansible_python_interpreter": sys.executable})
    finally:
        pool.shutdown()
    assert "ansible_system" in facts        # min subset includes system/distribution/pkg_mgr

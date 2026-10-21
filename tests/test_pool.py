import os
import subprocess
import sys

import pytest

from ansible_jsonrpc_server.pool import TaskPool
from ansible_jsonrpc_server.messages import Connection, HostSpec, RunParams

PING = RunParams(host=HostSpec(connection=Connection(type="local")),
                 fqcn="ansible.builtin.ping",
                 vars={"ansible_python_interpreter": sys.executable})


# --- probes run in the forked workers (module-level → picklable by reference) ---
def _worker_pid():
    return os.getpid()


def _pollute():
    import ansible_jsonrpc_server._preload as p
    p.leaked = "polluted"
    return os.getpid()


def _read_leak():
    import ansible_jsonrpc_server._preload as p
    return {"pid": os.getpid(), "leaked": getattr(p, "leaked", None)}


@pytest.fixture(scope="module")
def pool():
    p = TaskPool(forks=4)
    yield p
    p.shutdown()


def test_runs_module_through_pool(pool):
    res = pool.run_action(PING)
    assert res.status == "ok"
    assert res.result["ping"] == "pong"


def test_each_task_gets_a_fresh_worker_pid(pool):
    # max_tasks_per_child=1 → every task is a brand-new process, none the parent.
    parent = os.getpid()
    pids = [f.result() for f in [pool.submit(_worker_pid) for _ in range(8)]]
    assert all(pid != parent for pid in pids)
    assert len(set(pids)) == 8          # no worker handled two tasks


def test_in_memory_mutation_does_not_leak_between_tasks(pool):
    # A task mutates a preloaded module in its own process; the next task, running
    # in a fresh fork, must not see it — this is the ansible-global-state isolation.
    polluter_pid = pool.submit(_pollute).result()
    after = pool.submit(_read_leak).result()
    assert after["pid"] != polluter_pid
    assert after["leaked"] is None


def test_parent_process_never_imports_ansible():
    # Firewall: the parent owns the seam + pool but must not import ansible itself —
    # only the forkserver/workers do. Checked in a fresh interpreter (this pytest
    # process has ansible loaded from other tests).
    code = (
        "import sys;"
        "from ansible_jsonrpc_server.pool import TaskPool;"
        "from ansible_jsonrpc_server.messages import RunParams, HostSpec, Connection;"
        "p=TaskPool(forks=1);"
        "r=p.run_action(RunParams(host=HostSpec(connection=Connection(type='local')),"
        " fqcn='ansible.builtin.ping', vars={'ansible_python_interpreter': sys.executable}));"
        "p.shutdown();"
        "print(r.status, 'ansible' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(__file__)),  # server/ (package on path)
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok False"

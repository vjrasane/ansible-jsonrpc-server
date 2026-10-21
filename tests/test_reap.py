import os
import subprocess
import sys
import time

from ansible_jsonrpc_server.messages import Connection, HostSpec, RunParams
from ansible_jsonrpc_server.pool import TaskPool, _execute
from ansible_jsonrpc_server.reap import kill_descendants


def _pids_with(token):
    found = []
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % e, "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if token in cmd:
            found.append(int(e))
    return found


def _wait_until(pred, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_kill_descendants_kills_nested_subtree():
    token = "823.5"  # a unique sleep duration so we can find the OS process
    child = subprocess.Popen(["sh", "-c", "sleep %s" % token])  # sh → sleep: two levels
    try:
        assert _wait_until(lambda: _pids_with(token), 5), "subtree never started"
        kill_descendants(os.getpid())
        assert _wait_until(lambda: not _pids_with(token), 5), "subtree not reaped"
    finally:
        child.wait(timeout=5)
    assert child.returncode is not None  # the direct child (sh) died too


def test_cancel_reaps_the_module_subprocess():
    token = "771.3"
    params = RunParams(
        host=HostSpec(connection=Connection(type="local")),
        fqcn="ansible.builtin.command",
        args={"_raw_params": "sleep %s" % token},
        vars={"ansible_python_interpreter": sys.executable},
    )
    pool = TaskPool(forks=1)
    pool.prime()
    try:
        fut = pool.submit(_execute, params)
        assert _wait_until(lambda: _pids_with(token), 20), "module subprocess never ran"
        fut.cancel()  # pebble SIGTERMs the worker → harness reaper kills the subtree
        assert _wait_until(lambda: not _pids_with(token), 10), "sleep lingered after cancel"
    finally:
        pool.shutdown()

import sys
from ansible_jsonrpc_server.harness import run_action
from ansible_jsonrpc_server.messages import Connection, Flags, HostSpec, RunParams

LOCAL = HostSpec(connection=Connection(type="local"))
VARS = {"ansible_python_interpreter": sys.executable}


def _params(fqcn, args, flags=None):
    return RunParams(host=LOCAL, fqcn=fqcn, args=args, vars=VARS, flags=flags or Flags())


def test_ping_ok():
    out = run_action(_params("ansible.builtin.ping", {}))
    assert out.status == "ok"
    assert out.result["ping"] == "pong"


def test_command_changed_and_stdout():
    out = run_action(_params("ansible.builtin.command", {"_raw_params": "echo hi"}))
    assert out.status == "ok" and out.changed is True
    assert out.result["stdout"] == "hi"


def test_failing_command_is_failed_not_exception():
    out = run_action(_params("ansible.builtin.command", {"_raw_params": "false"}))
    assert out.status == "failed"
    assert out.result.get("rc") == 1


def test_no_log_redacts():
    out = run_action(_params("ansible.builtin.command",
                             {"_raw_params": "echo secret"}, flags=Flags(no_log=True)))
    assert "secret" not in str(out.result)


def test_timeout_returns_timed_out():
    out = run_action(_params("ansible.builtin.command",
                             {"_raw_params": "sleep 5"}, flags=Flags(timeout=1)))
    assert out.status == "timed_out"


def test_timeout_not_triggered_for_fast_task():
    out = run_action(_params("ansible.builtin.ping", {}, flags=Flags(timeout=10)))
    assert out.status == "ok"
    assert out.result["ping"] == "pong"


def test_timeout_through_pool_worker():
    from ansible_jsonrpc_server.pool import TaskPool

    pool = TaskPool(forks=1)
    try:
        out = pool.run_action(_params("ansible.builtin.command",
                                      {"_raw_params": "sleep 5"}, flags=Flags(timeout=1)))
    finally:
        pool.shutdown()
    assert out.status == "timed_out"

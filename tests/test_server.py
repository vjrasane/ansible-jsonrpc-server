import io
import sys
import time

from ansible_jsonrpc_server.messages import ResultFrame, RunParams
from ansible_jsonrpc_server.protocol import read_message, write_message
from ansible_jsonrpc_server.server import serve

LOCAL = {"connection": {"type": "local", "vars": {}}, "become": {}}
VARS = {"ansible_python_interpreter": sys.executable}


def _drive(requests, handlers=None):
    inbuf = io.BytesIO()
    for r in requests:
        write_message(inbuf, r)
    inbuf.seek(0)
    outbuf = io.BytesIO()
    serve(inbuf, outbuf, handlers=handlers)
    outbuf.seek(0)
    out = {}
    while True:
        m = read_message(outbuf)
        if m is None:
            break
        out[m.get("id")] = m          # responses are unordered now; key by id
    return out


def test_run_action_request_gets_result():
    out = _drive([
        {"jsonrpc": "2.0", "id": 1, "method": "runAction",
         "params": {"host": LOCAL, "fqcn": "ansible.builtin.ping", "args": {}, "vars": VARS}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ])
    assert out[1]["result"]["status"] == "ok"
    assert out[1]["result"]["result"]["ping"] == "pong"


def test_run_action_without_interpreter_discovers_it():
    # No ansible_python_interpreter and no gather: the server seeds ansible_facts so
    # ansible's own interpreter discovery runs instead of KeyError'ing.
    out = _drive([
        {"jsonrpc": "2.0", "id": 1, "method": "runAction",
         "params": {"host": LOCAL, "fqcn": "ansible.builtin.ping", "args": {}, "vars": {}}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ])
    assert out[1]["result"]["status"] == "ok"
    assert out[1]["result"]["result"]["ping"] == "pong"


def test_unknown_method_is_jsonrpc_error_and_survives():
    out = _drive([
        {"jsonrpc": "2.0", "id": 1, "method": "nope", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "runAction",
         "params": {"host": LOCAL, "fqcn": "ansible.builtin.ping", "args": {}, "vars": VARS}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ])
    assert out[1]["error"]["code"] == -32601        # unknown method → error
    assert out[2]["result"]["status"] == "ok"       # survived


def test_invalid_params_is_32602_and_survives():
    out = _drive([
        {"jsonrpc": "2.0", "id": 1, "method": "runAction", "params": {"host": LOCAL}},  # no fqcn
        {"jsonrpc": "2.0", "id": 2, "method": "runAction",
         "params": {"host": LOCAL, "fqcn": "ansible.builtin.ping", "args": {}, "vars": VARS}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ])
    assert out[1]["error"]["code"] == -32602        # validation at the seam
    assert out[2]["result"]["status"] == "ok"       # survived


def test_notification_without_id_gets_no_response():
    out = _drive([
        {"jsonrpc": "2.0", "method": "nope"},        # no id → a notification, never answered
        {"jsonrpc": "2.0", "id": 7, "method": "runAction",
         "params": {"host": LOCAL, "fqcn": "ansible.builtin.ping", "args": {}, "vars": VARS}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ])
    assert set(out) == {7}                           # only the request got a reply


def test_requests_are_dispatched_concurrently():
    # The reader must not block on execution: two slow handlers should overlap, so total
    # wall time is ~one handler, not two. Uses a fake slow handler (no ansible).
    def slow(_params):
        time.sleep(0.5)
        return ResultFrame(status="ok", changed=False, result={})

    handlers = {"slow": (RunParams, slow)}
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "slow",
         "params": {"host": LOCAL, "fqcn": "x"}},
        {"jsonrpc": "2.0", "id": 2, "method": "slow",
         "params": {"host": LOCAL, "fqcn": "x"}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ]
    start = time.monotonic()
    out = _drive(reqs, handlers=handlers)
    elapsed = time.monotonic() - start
    assert set(out) == {1, 2}
    assert elapsed < 0.9, "handlers ran serially (%.2fs), not concurrently" % elapsed


def test_cancellation_cancels_future_bound_after_the_cancel():
    from ansible_jsonrpc_server.cancellation import Cancellation

    class _FakeFuture:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    token = Cancellation()
    token.cancel()                 # cancel arrives before the task is submitted
    fut = _FakeFuture()
    token.bind(fut)                # future bound later → must be cancelled on bind
    assert fut.cancelled


def test_cancel_request_cancels_a_running_task(tmp_path):
    from ansible_jsonrpc_server.facts import make_handlers
    from ansible_jsonrpc_server.pool import TaskPool

    pool = TaskPool(forks=2)
    pool.prime()
    handlers = make_handlers(pool.run_action, str(tmp_path))
    try:
        start = time.monotonic()
        out = _drive([
            {"jsonrpc": "2.0", "id": 1, "method": "runAction",
             "params": {"host": LOCAL, "fqcn": "ansible.builtin.command",
                        "args": {"_raw_params": "sleep 30"}, "vars": VARS}},
            {"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 1}},
            {"jsonrpc": "2.0", "method": "shutdown"},
        ], handlers=handlers)
        elapsed = time.monotonic() - start
    finally:
        pool.shutdown()
    assert out[1]["result"]["status"] == "cancelled"
    assert elapsed < 20, "cancel did not short-circuit the 30s sleep (%.1fs)" % elapsed

import io
import sys

from ansible_jsonrpc_server.events import clear_emitter, emit, set_emitter
from ansible_jsonrpc_server.messages import Collection, ResultFrame, RunParams
from ansible_jsonrpc_server.protocol import read_message, write_message
from ansible_jsonrpc_server.server import serve

LOCAL = {"connection": {"type": "local", "vars": {}}, "become": {}}
VARS = {"ansible_python_interpreter": sys.executable}


def _drive_all(requests, handlers):
    # Keeps every frame in arrival order, so notifications (no id) are not collapsed.
    inbuf = io.BytesIO()
    for r in requests:
        write_message(inbuf, r)
    inbuf.seek(0)
    outbuf = io.BytesIO()
    serve(inbuf, outbuf, handlers=handlers)
    outbuf.seek(0)
    out = []
    while True:
        m = read_message(outbuf)
        if m is None:
            break
        out.append(m)
    return out


def test_handler_emit_produces_notification_and_response():
    def handler(_params):
        emit("progress", {"token": "t1", "phase": "begin"})
        emit("progress", {"token": "t1", "phase": "end"})
        return ResultFrame(status="ok", changed=False, result={})

    out = _drive_all([
        {"jsonrpc": "2.0", "id": 5, "method": "go", "params": {"host": LOCAL, "fqcn": "x"}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ], handlers={"go": (RunParams, handler)})

    notes = [m for m in out if "id" not in m]
    resps = [m for m in out if m.get("id") == 5]
    assert [n["params"]["phase"] for n in notes] == ["begin", "end"]
    assert all(n["method"] == "progress" for n in notes)
    assert len(resps) == 1 and resps[0]["result"]["status"] == "ok"


def test_notification_carries_originating_request_id():
    def handler(_params):
        emit("progress", {"token": "t1", "phase": "begin"})
        return ResultFrame(status="ok", changed=False, result={})

    out = _drive_all([
        {"jsonrpc": "2.0", "id": 9, "method": "go", "params": {"host": LOCAL, "fqcn": "x"}},
        {"jsonrpc": "2.0", "method": "shutdown"},
    ], handlers={"go": (RunParams, handler)})

    note = next(m for m in out if "id" not in m)
    assert note["params"]["requestId"] == 9


def test_emit_off_request_thread_is_noop():
    clear_emitter()
    emit("progress", {"token": "t", "phase": "begin"})  # must not raise with no emitter


def test_collection_install_emits_begin_end():
    from ansible_jsonrpc_server.collections import CollectionCache

    class _Proc:
        returncode = 0
        stderr = ""

    events = []
    set_emitter(lambda method, params: events.append((method, params)), req_id=3)
    try:
        cache = CollectionCache("/nonexistent", run=lambda *a, **k: _Proc())
        cache.ensure(Collection(name="ns.coll", version="1.0.0"))
    finally:
        clear_emitter()

    phases = [(p["kind"], p["phase"], p.get("ok")) for _, p in events]
    assert phases == [("collection", "begin", None), ("collection", "end", True)]
    assert all(p["requestId"] == 3 for _, p in events)

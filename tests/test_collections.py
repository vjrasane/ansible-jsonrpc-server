import json
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from ansible_jsonrpc_server.collections import CollectionCache
from ansible_jsonrpc_server.messages import Collection

COLL = Collection(name="community.general", version="9.4.0")
SERVER_DIR = os.path.dirname(os.path.dirname(__file__))

_HELLO_MODULE = """\
#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule
def main():
    AnsibleModule(argument_spec={}).exit_json(changed=False, hello="world")
if __name__ == "__main__":
    main()
"""


def _ok(*a, **k):
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _write_manifest(path, name, version):
    ns, coll = name.split(".", 1)
    d = os.path.join(path, "ansible_collections", ns, coll)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "MANIFEST.json"), "w") as f:
        json.dump({"collection_info": {"version": version}}, f)


def test_single_flight_one_install_for_concurrent_same_collection(tmp_path):
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        time.sleep(0.1)
        return _ok()

    cache = CollectionCache(str(tmp_path), run=run)
    start = threading.Barrier(5)

    def worker():
        start.wait()
        cache.ensure(COLL)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1          # one install, not five


def test_skips_when_already_present_at_version(tmp_path):
    calls = []
    _write_manifest(str(tmp_path), COLL.name, COLL.version)

    def run(cmd, **k):
        calls.append(cmd)
        return _ok()

    CollectionCache(str(tmp_path), run=run).ensure(COLL)
    assert calls == []              # on-disk hit → no galaxy call


def test_builds_install_command(tmp_path):
    captured = {}

    def run(cmd, **k):
        captured["cmd"] = cmd
        return _ok()

    CollectionCache(str(tmp_path), run=run, galaxy="ansible-galaxy").ensure(COLL)
    assert captured["cmd"] == [
        sys.executable, "ansible-galaxy", "collection", "install",
        "community.general:9.4.0", "-p", str(tmp_path),
    ]


def test_source_adds_server_flag(tmp_path):
    captured = {}

    def run(cmd, **k):
        captured["cmd"] = cmd
        return _ok()

    coll = Collection(name="my.coll", version="1.0.0", source="https://hub.example/api/")
    CollectionCache(str(tmp_path), run=run, galaxy="ansible-galaxy").ensure(coll)
    assert captured["cmd"][-2:] == ["--server", "https://hub.example/api/"]


def test_failure_is_evicted_so_next_call_retries(tmp_path):
    state = {"n": 0}

    def run(cmd, **k):
        state["n"] += 1
        rc = 1 if state["n"] == 1 else 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="boom")

    cache = CollectionCache(str(tmp_path), run=run)
    with pytest.raises(RuntimeError):
        cache.ensure(COLL)
    cache.ensure(COLL)              # retried, not a cached failure
    assert state["n"] == 2


def test_make_handlers_installs_collection_before_run(tmp_path, monkeypatch):
    from ansible_jsonrpc_server.facts import make_handlers
    from ansible_jsonrpc_server.messages import Connection, HostSpec, ResultFrame, RunParams

    calls = []
    monkeypatch.setattr(
        "ansible_jsonrpc_server.collections.subprocess.run",
        lambda cmd, **k: calls.append(cmd) or types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    _, handler = make_handlers(lambda p: ResultFrame(status="ok", changed=False, result={}),
                               str(tmp_path))["runAction"]
    handler(RunParams(host=HostSpec(connection=Connection(type="local")),
                      fqcn="community.general.foo", collection=COLL))
    assert calls[0][2:5] == ["collection", "install", "community.general:9.4.0"]


def _lay_out_collection(root):
    # A trivial installed collection: ansible_collections/t/c with one module.
    d = os.path.join(root, "ansible_collections", "t", "c")
    os.makedirs(os.path.join(d, "plugins", "modules"), exist_ok=True)
    with open(os.path.join(d, "MANIFEST.json"), "w") as f:
        json.dump({"collection_info": {"version": "1.0.0"}}, f)
    with open(os.path.join(d, "plugins", "modules", "hello.py"), "w") as f:
        f.write(_HELLO_MODULE)


def test_runs_collection_module_end_to_end(tmp_path):
    # Pre-install a local collection, spawn the real server pointed at it, and run the
    # collection module over JSON-RPC — proves the startup env + worker loader resolve
    # a collection module (and would its action plugin) from the collections path.
    from ansible_jsonrpc_server.protocol import read_message, write_message

    # server data dir = $XDG_DATA_HOME/ansible-jsonrpc-server → collections/ under it
    _lay_out_collection(os.path.join(str(tmp_path), "ansible-jsonrpc-server", "collections"))
    env = {**os.environ, "XDG_DATA_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ansible_jsonrpc_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=SERVER_DIR, env=env,
    )
    try:
        write_message(proc.stdin, {
            "jsonrpc": "2.0", "id": 1, "method": "runAction",
            "params": {"host": {"connection": {"type": "local"}}, "fqcn": "t.c.hello",
                       "vars": {"ansible_python_interpreter": sys.executable}},
        })
        reply = read_message(proc.stdout)
        write_message(proc.stdin, {"jsonrpc": "2.0", "method": "shutdown"})
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert reply["id"] == 1
    assert reply["result"]["status"] == "ok"
    assert reply["result"]["result"]["hello"] == "world"

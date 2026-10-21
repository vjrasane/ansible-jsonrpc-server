import subprocess
import sys
from ansible_jsonrpc_server.protocol import read_message, write_message

LOCAL = {"connection": {"type": "local", "vars": {}}, "become": {}}
VARS = {"ansible_python_interpreter": sys.executable}


def test_spawned_server_runs_ping_and_stray_print_does_not_corrupt():
    proc = subprocess.Popen(
        [sys.executable, "-m", "ansible_jsonrpc_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        write_message(proc.stdin, {
            "jsonrpc": "2.0", "id": 1, "method": "runAction",
            "params": {"host": LOCAL, "fqcn": "ansible.builtin.command",
                       "args": {"_raw_params": "echo hi"}, "vars": VARS},
        })
        reply = read_message(proc.stdout)
        write_message(proc.stdin, {"jsonrpc": "2.0", "method": "shutdown"})
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert reply["id"] == 1
    assert reply["result"]["status"] == "ok"
    assert reply["result"]["result"]["stdout"] == "hi"   # frame uncorrupted

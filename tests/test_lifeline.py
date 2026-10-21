import os
import signal
import subprocess
import sys
import time

SERVER_DIR = os.path.dirname(os.path.dirname(__file__))  # server/ (package on path)

# A "client": spawns the server, prints its pid, sends one long-running task so a
# worker (and the `sleep` it spawns) is live, then idles. Killing this client
# simulates client death — the server must take its whole process group down.
CLIENT_CODE = r"""
import json, subprocess, sys, time
p = subprocess.Popen(
    [sys.executable, "-m", "ansible_jsonrpc_server"],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print(p.pid, flush=True)
req = {"jsonrpc": "2.0", "id": 1, "method": "runAction", "params": {
    "host": {"connection": {"type": "local", "vars": {}}, "become": {}},
    "fqcn": "ansible.builtin.command", "args": {"_raw_params": "sleep 30"},
    "vars": {"ansible_python_interpreter": sys.executable}}}
body = json.dumps(req).encode()
p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
p.stdin.flush()
time.sleep(3600)
"""


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait(pred, timeout, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pred():
            return True
        time.sleep(interval)
    return not pred()


def _reap_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def test_server_tree_dies_when_client_dies():
    client = subprocess.Popen(
        [sys.executable, "-c", CLIENT_CODE],
        stdout=subprocess.PIPE, cwd=SERVER_DIR, text=True,
    )
    server_pid = int(client.stdout.readline())
    try:
        time.sleep(4)  # arm (setpgrp) + prime forkserver + worker starts `sleep`
        assert _group_alive(server_pid), "lifeline never established a process group"
        client.kill()  # SIGKILL the client → server's parent is gone
        assert _wait(lambda: _group_alive(server_pid), timeout=10), \
            "server tree survived client death"
    finally:
        client.kill()
        _reap_group(server_pid)


def test_sigterm_tears_down_tree():
    proc = subprocess.Popen(
        [sys.executable, "-m", "ansible_jsonrpc_server"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=SERVER_DIR,
    )
    try:
        time.sleep(3)  # arm + prime
        assert _group_alive(proc.pid)
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=8)  # client reaps its child (else the dead leader lingers as a zombie)
        assert _wait(lambda: _group_alive(proc.pid), timeout=5), \
            "process group leaked after SIGTERM"
    finally:
        _reap_group(proc.pid)
        proc.kill()


def test_stdin_eof_exits():
    proc = subprocess.Popen(
        [sys.executable, "-m", "ansible_jsonrpc_server"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=SERVER_DIR,
    )
    try:
        time.sleep(3)
        assert _group_alive(proc.pid)
        proc.stdin.close()  # EOF on the seam
        proc.wait(timeout=8)  # client reaps its child
        assert _wait(lambda: _group_alive(proc.pid), timeout=5), \
            "did not shut down on stdin EOF"
    finally:
        _reap_group(proc.pid)
        proc.kill()

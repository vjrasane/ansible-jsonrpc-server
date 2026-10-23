import os
import subprocess
import sys
import textwrap

from ansible_jsonrpc_server.protocol import read_message, write_message

# A collection installed at RUNTIME (after init_plugin_loader) is only discoverable if the
# `ansible_collections/` dir already existed at init — the namespace package fixes its
# __path__ then. The server pre-creates that dir before priming the forkserver; these
# tests pin both the mechanism (why) and the wiring (that the server does it).

# Run the probe in a subprocess: init_plugin_loader mutates process-global import state.
_PROBE = textwrap.dedent(
    """
    import os, sys
    cp, precreate = sys.argv[1], sys.argv[2] == "1"
    os.makedirs(os.path.join(cp, "ansible_collections") if precreate else cp, exist_ok=True)
    os.environ["ANSIBLE_COLLECTIONS_PATH"] = cp
    os.environ["ANSIBLE_HOME"] = os.path.join(cp, "home")
    import ansible.plugins.loader as loader
    loader.init_plugin_loader()                      # namespace __path__ fixed here
    d = os.path.join(cp, "ansible_collections", "testns", "testcoll", "plugins", "modules")
    os.makedirs(d, exist_ok=True)                    # "install" a collection AFTER init
    with open(os.path.join(d, "hello.py"), "w") as f:
        f.write("from ansible.module_utils.basic import AnsibleModule\\n")
    from ansible.plugins.loader import module_loader
    ctx = module_loader.find_plugin_with_context("testns.testcoll.hello")
    print("RESOLVED" if ctx.resolved else "UNRESOLVED")
    """
)


def _probe(tmp_path, precreate: bool) -> str:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tmp_path), "1" if precreate else "0"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


def test_runtime_collection_invisible_without_precreated_dir(tmp_path):
    # Proves the scenario reproduces the bug — otherwise the fix test below is vacuous.
    assert _probe(tmp_path, precreate=False) == "UNRESOLVED"


def test_runtime_collection_resolves_when_dir_precreated(tmp_path):
    assert _probe(tmp_path, precreate=True) == "RESOLVED"


def test_server_precreates_ansible_collections_dir(tmp_path):
    # The spawned server must create collections/ansible_collections at startup so a
    # runtime collection install is discoverable by its workers.
    env = {**os.environ, "XDG_DATA_HOME": str(tmp_path / "data"),
           "XDG_CACHE_HOME": str(tmp_path / "cache")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ansible_jsonrpc_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
    )
    try:
        write_message(proc.stdin, {
            "jsonrpc": "2.0", "id": 1, "method": "runAction",
            "params": {"host": {"connection": {"type": "local", "vars": {}}, "become": {}},
                       "fqcn": "ansible.builtin.command", "args": {"_raw_params": "echo hi"},
                       "vars": {"ansible_python_interpreter": sys.executable}},
        })
        read_message(proc.stdout)  # a reply means startup completed (dir created)
        write_message(proc.stdin, {"jsonrpc": "2.0", "method": "shutdown"})
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    ac = tmp_path / "data" / "ansible-jsonrpc-server" / "collections" / "ansible_collections"
    assert ac.is_dir()

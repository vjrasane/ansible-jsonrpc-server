# DEFERRED (Plan: delegate connection). Unused in native execution; retained as a
# shape reference for the future delegate transport (server->client exec/put/fetch).
# See docs/design-action-plugin-harness.md §4.4.
import base64
import ansible.plugins.loader as loader
from ansible.plugins.connection import ConnectionBase


class ShimConnection(ConnectionBase):
    transport = "jsonrpc_delegate"
    has_pipelining = False

    def __init__(self, play_context, rpc):
        super().__init__(play_context)
        self._rpc = rpc
        self._shell = loader.shell_loader.get("sh")

    def _connect(self):
        self._connected = True
        return self

    def close(self):
        self._connected = False

    def exec_command(self, cmd, in_data=None, sudoable=True):
        params = {
            "cmd": cmd,
            "in_data": base64.b64encode(in_data).decode() if in_data else None,
            "sudoable": sudoable,
        }
        r = self._rpc.call("exec_command", params)
        return (r["rc"], base64.b64decode(r["stdout"]), base64.b64decode(r["stderr"]))

    def put_file(self, in_path, out_path):
        self._rpc.call("put_file", {"in_path": in_path, "out_path": out_path})

    def fetch_file(self, in_path, out_path):
        self._rpc.call("fetch_file", {"in_path": in_path, "out_path": out_path})

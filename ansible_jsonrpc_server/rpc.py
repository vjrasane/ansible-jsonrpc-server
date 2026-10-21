# DEFERRED (Plan: delegate connection). Unused in native execution; retained as a
# shape reference for the future delegate transport (server->client exec/put/fetch).
# See docs/design-action-plugin-harness.md §4.4. The socket frame codec this relied on
# (send_frame/recv_frame) was removed with the JSON-RPC migration; the delegate will be
# rebuilt on the bidirectional JSON-RPC seam, so these calls are placeholders.
import itertools


class RpcClient:
    def __init__(self, sock):
        self._sock = sock
        self._ids = itertools.count(1)

    def call(self, op, params):
        req_id = next(self._ids)
        send_frame(self._sock, {"type": "request", "id": req_id, "op": op, "params": params})
        resp = recv_frame(self._sock)
        if resp is None:
            raise ConnectionError("server closed during %s" % op)
        if resp.get("id") != req_id:
            raise RuntimeError("rpc id mismatch: sent %s got %s" % (req_id, resp.get("id")))
        if not resp.get("ok", False):
            raise RuntimeError("rpc %s failed: %s" % (op, resp.get("error")))
        return resp.get("result")

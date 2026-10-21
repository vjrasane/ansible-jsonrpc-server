import threading
from collections.abc import Callable

# Server→client notifications (design §4.5.1). The single writer lives in serve(); it
# sets a per-request emit context here (notify + originating request id, like the
# cancellation token), so the ansible-free caches can push progress events through the
# same write lock without holding a reference to the seam.

Notify = Callable[[str, dict], None]

_current = threading.local()


def set_emitter(notify: Notify, req_id: object) -> None:
    _current.notify = notify
    _current.req_id = req_id


def clear_emitter() -> None:
    _current.notify = None
    _current.req_id = None


def emit(method: str, params: dict) -> None:
    """Emit a JSON-RPC notification, stamping the originating request id so the client
    can attribute the event to a task. No-op off a request thread (no emitter bound)."""
    notify = getattr(_current, "notify", None)
    if notify is None:
        return
    notify(method, {"requestId": getattr(_current, "req_id", None), **params})

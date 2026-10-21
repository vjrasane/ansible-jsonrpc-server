import threading


class Cancellation:
    """Carries a request's cancel state down to the pool future running its task.
    `cancel()` can arrive before the future exists (the dispatch thread hasn't submitted
    to the pool yet), so we record the intent and cancel the future on `bind`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._future = None
        self._cancelled = False

    def bind(self, future) -> None:
        with self._lock:
            self._future = future
            if self._cancelled:
                future.cancel()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._future is not None:
                self._future.cancel()


# The dispatch thread sets its request's token here; the pool (same thread) binds the
# task future to it, so cancellation needs no change to handler signatures.
_current = threading.local()


def set_current(token: Cancellation | None) -> None:
    _current.token = token


def bind_future(future) -> None:
    token = getattr(_current, "token", None)
    if token is not None:
        token.bind(future)

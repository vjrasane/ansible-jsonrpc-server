import ctypes
import ctypes.util
import os
import signal
import threading
import time

_PR_SET_PDEATHSIG = 1


def arm(death_signal: int = signal.SIGTERM) -> None:
    """Orphan-prevention, part 1 — no threads. Make this process its own group
    leader (so the forkserver + workers join its group), tear the whole group down
    on SIGTERM, and on Linux ask the kernel to signal us the instant our parent
    dies. Call BEFORE the pool/forkserver starts so they inherit the group, and
    before any thread exists (fork-after-threads hazard)."""
    try:
        os.setpgrp()
    except OSError:
        pass
    signal.signal(signal.SIGTERM, _on_term)
    _set_pdeathsig(death_signal)
    if os.getppid() == 1:  # race: parent already died before we armed
        terminate()


def watch_parent(interval: float = 0.5) -> threading.Thread:
    """Orphan-prevention, part 2 — the portable fallback for PR_SET_PDEATHSIG (e.g.
    macOS) and a backstop for the arm-time race. Start AFTER the forkserver is up."""
    original = os.getppid()
    t = threading.Thread(target=_poll, args=(original, interval), daemon=True)
    t.start()
    return t


def _poll(original_ppid: int, interval: float) -> None:
    while os.getppid() == original_ppid:
        time.sleep(interval)
    terminate()  # reparented → our client is gone


def _on_term(signum, frame) -> None:
    terminate()


def terminate() -> None:
    # Nuke the whole group — server + forkserver + workers (+ any subprocess a module
    # spawned) — as one unit. A worker mid-task holding an SSH master can't be reaped
    # gracefully, so SIGKILL the group. This kills us too, which is the point.
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except OSError:
        os._exit(1)


def _set_pdeathsig(sig: int) -> None:
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, sig)
    except (OSError, TypeError, AttributeError):
        pass  # non-Linux: watch_parent() covers it

import os
import signal
import threading

# Promptly free an interrupted task's child subtree (design §4.10). On cancel or a hard
# timeout pebble SIGTERMs the worker; on a soft timeout the task abandons a still-running
# module. In both, the task's own descendants (e.g. `command: sleep`) would otherwise
# linger in the server's process group until teardown. We SIGKILL only the worker's
# descendants — not a shared ControlPersist ssh master, which self-detaches (setsid) and
# so is not a descendant. Linux-only (/proc); elsewhere the §4.6 lifeline still bounds
# orphans at server exit.

_prev_term: object = None


def kill_descendants(pid: int | None = None) -> None:
    root = os.getpid() if pid is None else pid
    children = _children_by_parent()
    stack = list(children.get(root, ()))
    victims: list[int] = []
    while stack:
        p = stack.pop()
        victims.append(p)
        stack.extend(children.get(p, ()))
    for p in victims:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


def install_sigterm_reaper() -> None:
    """Reap descendants on pebble's SIGTERM (cancel / hard timeout). Scoped to the run
    (uninstalled after) so a clean completion's worker recycle never reaps. No-op off
    the main thread — `signal` only works there, and the real task always runs in the
    worker's main thread; the in-process (test) handler path runs on dispatch threads."""
    global _prev_term
    if threading.current_thread() is not threading.main_thread():
        return
    _prev_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _on_term)


def uninstall_sigterm_reaper() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    if _prev_term is not None:
        signal.signal(signal.SIGTERM, _prev_term)  # type: ignore[arg-type]


def _on_term(signum, frame) -> None:
    kill_descendants()
    if callable(_prev_term):
        _prev_term(signum, frame)  # pebble's process_exit: multiprocessing cleanup + exit
    os._exit(-signum)


def _children_by_parent() -> dict[int, list[int]]:
    tree: dict[int, list[int]] = {}
    try:
        pids = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return tree  # no /proc (non-Linux) → caller no-ops
    for p in pids:
        ppid = _ppid(p)
        if ppid is not None:
            tree.setdefault(ppid, []).append(p)
    return tree


def _ppid(pid: int) -> int | None:
    try:
        with open("/proc/%d/stat" % pid) as f:
            data = f.read()
    except OSError:
        return None
    # comm (field 2) is parenthesized and may itself contain spaces/parens; ppid is the
    # second whitespace field after the final ')'.
    try:
        return int(data[data.rindex(")") + 2:].split()[1])
    except (ValueError, IndexError):
        return None

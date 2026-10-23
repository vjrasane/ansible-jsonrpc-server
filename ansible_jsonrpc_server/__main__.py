import os
import sys
from . import lifeline
from .paths import cache_dir, data_dir
from .server import serve
from .pool import TaskPool
from .facts import make_handlers


def _env_int(name: str, default: int) -> int:
    # Config lives at the edge; the library keeps its pure defaults. Reject
    # non-positive / malformed values so a typo fails loud, not as a dead server.
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a positive integer, got {raw!r}")
    if value < 1:
        raise SystemExit(f"{name} must be >= 1, got {value}")
    return value


def main() -> None:
    # Protect the protocol stream: keep a private dup of stdout for the seam, then
    # point fd 1 (and sys.stdout) at stderr so a stray print() — in this process OR
    # in a forked worker, which inherits fd 1 — can never corrupt the frames.
    in_fd = os.dup(sys.stdin.fileno())
    out_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), 1)
    sys.stdout = sys.stderr

    # Point ansible's dirs at the server's own tree, set BEFORE the forkserver's
    # init_plugin_loader. We *override* (not inherit) these: the user's / devenv's
    # ANSIBLE_COLLECTIONS_PATH and ANSIBLE_HOME are for their ansible, not ours — the
    # server manages its own pinned collections and must never read/write a foreign
    # location, nor leak its hard-kill tmp litter into ~/.ansible. Collections are
    # installed deps → data dir; ansible scratch → cache dir (safe to purge).
    collections_path = os.path.join(data_dir(), "collections")
    ansible_home = os.path.join(cache_dir(), "ansible")
    os.makedirs(collections_path, exist_ok=True)
    os.environ["ANSIBLE_COLLECTIONS_PATH"] = collections_path
    os.environ["ANSIBLE_HOME"] = ansible_home
    os.environ["ANSIBLE_LOCAL_TEMP"] = os.path.join(ansible_home, "tmp")
    # Discover the target interpreter silently (we cache it anyway); the default "auto"
    # prints a per-cold-host warning. A client-set interpreter still overrides this.
    os.environ.setdefault("ANSIBLE_PYTHON_INTERPRETER", "auto_silent")

    # Orphan-prevention: own process group + SIGTERM/PR_SET_PDEATHSIG, armed while
    # single-threaded and before the pool so the forkserver/workers join the group.
    lifeline.arm()
    pool = TaskPool(forks=_env_int("ANSIBLE_JSONRPC_FORKS", 5))
    pool.prime()          # forkserver up while single-threaded (fork-after-threads)
    lifeline.watch_parent()  # getppid fallback — safe to start a thread now
    handlers = make_handlers(pool.run_action, collections_path)  # install + facts + run
    try:
        with os.fdopen(in_fd, "rb", buffering=0) as in_stream, \
                os.fdopen(out_fd, "wb", buffering=0) as out_stream:
            serve(in_stream, out_stream, handlers=handlers,
                  max_inflight=_env_int("ANSIBLE_JSONRPC_MAX_INFLIGHT", 64))
    finally:
        # Tear down the whole group — graceful exit (EOF/shutdown) still leaves the
        # forkserver + workers alive otherwise. pool.shutdown() can't reap a worker
        # mid-task; killpg does. This SIGKILLs us too, which is the intended exit.
        lifeline.terminate()


if __name__ == "__main__":
    main()

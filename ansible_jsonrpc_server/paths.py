import os

# The server owns two XDG-addressed dirs (design §4.8), split by what a purge costs:
#   data  — installed deps that must survive (venv, pinned collections); deleting it
#           means a re-bootstrap / re-install.
#   cache — ansible's own scratch (AnsiballZ cache, local tmp, CP sockets, incl. our
#           hard-kill tmp litter); always safe to purge, reaped where the OS cleans.
# Keeping scratch in cache (not ~/.ansible) keeps our litter out of the user's home;
# keeping deps in data means `rm -rf ~/.cache` never breaks the server.
# Relocate via XDG_DATA_HOME / XDG_CACHE_HOME; no bespoke env var.
_APP = "ansible-jsonrpc-server"


def data_dir() -> str:
    root = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(root, _APP)


def cache_dir() -> str:
    root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(root, _APP)

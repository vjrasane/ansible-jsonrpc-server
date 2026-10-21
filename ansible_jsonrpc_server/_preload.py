# Imported in the forkserver process so every worker forks warm (copy-on-write):
# ansible and the plugin loader are already initialized, so a task costs a fork,
# not a fresh import. Never imported by the parent — only the forkserver loads it.
import ansible.plugins.loader as _loader
from . import harness as _harness  # noqa: F401 — pulls in context/result + ansible

_loader.init_plugin_loader()

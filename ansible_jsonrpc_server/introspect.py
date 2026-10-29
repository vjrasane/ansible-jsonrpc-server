import json
import os
import sys
from pathlib import Path
from typing import cast

from .paths import data_dir

# Point ansible at the server's own installed-collections tree (shared with run, so a
# generated collection is already warm) BEFORE importing ansible: the plugin loader
# reads ANSIBLE_COLLECTIONS_PATH from config at import, and a value set afterwards is
# ignored. The ansible_collections dir must also exist before init_plugin_loader freezes
# the namespace __path__.
COLLECTIONS_PATH = os.path.join(data_dir(), "collections")
os.makedirs(os.path.join(COLLECTIONS_PATH, "ansible_collections"), exist_ok=True)
os.environ["ANSIBLE_COLLECTIONS_PATH"] = COLLECTIONS_PATH

from ansible.executor.module_common import modify_module  # noqa: E402
from ansible.parsing.dataloader import DataLoader  # noqa: E402
from ansible.plugins.list import list_plugins  # noqa: E402
from ansible.plugins.loader import (  # noqa: E402
    action_loader,
    fragment_loader,
    init_plugin_loader,
    module_loader,
)
from ansible.template import Templar  # noqa: E402
from ansible.utils.plugin_docs import get_docstring  # noqa: E402

from .collections import CollectionCache  # noqa: E402
from .messages import Collection  # noqa: E402


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def process_module(fqcn: str, collection: str) -> dict:
    """Introspect one module into a slim record: identity, style, meta, and the
    documented option/return types. The server owns ansible-core and builds its own
    execution payload, so no source closure is extracted."""
    log(f"  introspecting {fqcn}")
    ctx = module_loader.find_plugin_with_context(fqcn)
    path = cast(str, ctx.plugin_resolved_path)

    d, _, r, _ = get_docstring(
        path, fragment_loader, plugin_type="module", collection_name=collection
    )
    doc = cast(dict, d) or {}
    returndocs = cast(dict, r) or {}
    attrs = doc.get("attributes") or {}

    actx = action_loader.find_plugin_with_context(fqcn)
    apath = cast(str, actx.plugin_resolved_path) if actx.resolved else None
    action_plugin = apath is not None and Path(apath).stem != "normal"
    powershell = path.endswith(".ps1")
    raw_params = "free_form" in (doc.get("options") or {})
    check_mode = (attrs.get("check_mode", {}) or {}).get("support", "none")

    meta = {
        "fqcn": fqcn,
        "actionPlugin": action_plugin,
        "powershell": powershell,
        "rawParams": raw_params,
        "checkMode": check_mode,
    }

    built = modify_module(
        module_name=fqcn,
        module_path=path,
        module_args={},  # sentinel; we only read the resolved module style
        templar=Templar(loader=DataLoader()),
        task_vars={"ansible_python_interpreter": "/usr/bin/python3"},
    )
    style = "new" if built.module_style == "new" else "old"

    return {
        "fqcn": fqcn,
        "style": style,
        "meta": meta,
        "options": doc.get("options") or {},
        "returndocs": returndocs,
    }


def main() -> None:
    job = json.loads(sys.argv[1])
    collection: str = job["collection"]
    version: str | None = job.get("version")
    source: str | None = job.get("source")

    # Install, THEN init the loader. The running server installs per-request and then
    # forks a fresh worker that loads from the populated tree; a single introspect process
    # has no such fork, so init must follow the install or the loader caches the collection
    # as absent. ANSIBLE_COLLECTIONS_PATH / the namespace dir were set at import (above).
    cache = CollectionCache(COLLECTIONS_PATH)
    resolved = (cache.ensure(Collection(name=collection, version=version, source=source))
                if version else cache.ensure_latest(collection, source))
    init_plugin_loader()

    names = list(list_plugins("module", [collection]).keys())
    if not names:
        log(f"no modules for {collection}")
        sys.exit(1)

    modules = []
    for fqcn in names:
        try:
            modules.append(process_module(fqcn, collection))
        except Exception as e:
            log(f"error on {fqcn}: {e}")
            raise

    json.dump({"collection": collection, "version": resolved, "modules": modules},
              sys.stdout, default=str)


if __name__ == "__main__":
    main()

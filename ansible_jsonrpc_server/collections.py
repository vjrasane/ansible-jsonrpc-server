import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future

from .events import emit
from .messages import Collection

# Collection acquisition, server-owned (design §4.8.1). Lives in the ansible-free
# parent: it imports no ansible, it shells out to the venv's ansible-galaxy, which
# downloads + verifies (against Galaxy's hash) + extracts + resolves dependencies.
_GALAXY = os.path.join(sys.prefix, "bin", "ansible-galaxy")


class CollectionCache:
    """Ensures a pinned collection+version is installed on the collections path, once,
    shared across tasks. Single-flight per collection name (one version per session,
    §4.8.1); evict on failure so the next task retries."""

    def __init__(self, collections_path: str,
                 run: Callable | None = None, galaxy: str = _GALAXY):
        self._path = collections_path
        # Resolve subprocess.run at call time (not as a default arg bound at import),
        # so it stays monkeypatchable; tests inject their own `run`.
        self._run = run
        self._galaxy = galaxy
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def ensure(self, coll: Collection) -> None:
        with self._lock:
            fut = self._futures.get(coll.name)
            owner = fut is None
            if owner:
                fut = Future()
                self._futures[coll.name] = fut
        if owner:
            try:
                fut.set_result(self._install(coll))
            except BaseException as exc:  # noqa: BLE001 — evict then propagate
                with self._lock:
                    self._futures.pop(coll.name, None)
                fut.set_exception(exc)
        return fut.result()

    def _install(self, coll: Collection) -> None:
        if self._present(coll.name, coll.version):
            return
        token = "collection:%s" % coll.name
        emit("progress", {"token": token, "phase": "begin", "kind": "collection",
                          "name": coll.name, "version": coll.version})
        # Run the galaxy console script through our own interpreter rather than
        # exec'ing it directly, so a stale/relocated shebang can never ENOENT.
        cmd = [sys.executable, self._galaxy, "collection", "install",
               "%s:%s" % (coll.name, coll.version), "-p", self._path]
        if coll.source:
            cmd += ["--server", coll.source]
        run = self._run or subprocess.run
        proc = run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            emit("progress", {"token": token, "phase": "end", "kind": "collection",
                              "ok": False})
            raise RuntimeError(
                "ansible-galaxy install %s:%s failed:\n%s"
                % (coll.name, coll.version, proc.stderr)
            )
        emit("progress", {"token": token, "phase": "end", "kind": "collection",
                          "ok": True})

    def _present(self, name: str, version: str) -> bool:
        ns, coll = name.split(".", 1)
        manifest = os.path.join(self._path, "ansible_collections", ns, coll, "MANIFEST.json")
        try:
            with open(manifest) as f:
                return json.load(f)["collection_info"]["version"] == version
        except (FileNotFoundError, KeyError, ValueError):
            return False

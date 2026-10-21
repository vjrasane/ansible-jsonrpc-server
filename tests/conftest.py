import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_ansible_state(tmp_path_factory):
    # Keep every ansible side effect inside a throwaway dir, so tests never write into
    # the repo (devenv points ANSIBLE_COLLECTIONS_PATH at $REPO/collections) or
    # ~/.ansible. Relocate the server's cache/data via XDG (what the server honors), and
    # set the ansible dirs directly too for in-process tests that don't spawn the server.
    # Set before any pool/forkserver starts so workers inherit it.
    base = tmp_path_factory.mktemp("xdg")
    os.environ["XDG_DATA_HOME"] = str(base / "data")
    os.environ["XDG_CACHE_HOME"] = str(base / "cache")

    from ansible_jsonrpc_server.paths import cache_dir, data_dir
    os.environ["ANSIBLE_HOME"] = os.path.join(cache_dir(), "ansible")
    os.environ["ANSIBLE_LOCAL_TEMP"] = os.path.join(cache_dir(), "ansible", "tmp")
    os.environ["ANSIBLE_COLLECTIONS_PATH"] = os.path.join(data_dir(), "collections")
    yield

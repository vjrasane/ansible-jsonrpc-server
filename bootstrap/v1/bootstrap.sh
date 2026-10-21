#!/bin/sh
# Provision the ansible-jsonrpc-server runtime: an isolated, version-keyed venv with the
# server (and ansible-core) installed. Idempotent — a second run is a fast no-op. Prints
# the venv's python path on the last stdout line so the caller knows what to spawn.
# Client-agnostic and version-agnostic: the caller passes the release to provision, so
# this one script serves every client and every version. POSIX sh.
#
# Usage: bootstrap.sh <version> [python]
#   <version>  ansible-jsonrpc-server release to install (PyPI pin + venv key)
#   [python]   optional interpreter; else newest discovered 3.13→3.11 (require >= 3.11)
#
# ANSIBLE_JSONRPC_SERVER_SRC=<path> installs that checkout editable instead (server
# development), keyed under "dev" so it never collides with a released venv.
set -eu

version=${1:-}
py=${2:-}

if [ -n "${ANSIBLE_JSONRPC_SERVER_SRC:-}" ]; then
  install_spec="-e $ANSIBLE_JSONRPC_SERVER_SRC"
  key=dev
else
  [ -n "$version" ] || { echo "bootstrap: version argument required" >&2; exit 2; }
  install_spec="ansible-jsonrpc-server==$version"
  key=$version
fi

# Installed deps live in the data dir (survive a cache purge), keyed by version.
data_home=${XDG_DATA_HOME:-$HOME/.local/share}
root="$data_home/ansible-jsonrpc-server"
venv="$root/venv/$key"

# Python: explicit arg override, else newest discovered 3.13→3.11; require >= 3.11.
if [ -z "$py" ]; then
  for c in python3.13 python3.12 python3.11; do
    if command -v "$c" >/dev/null 2>&1; then py=$c; break; fi
  done
fi
[ -n "$py" ] || { echo "bootstrap: no python3.11+ interpreter found" >&2; exit 1; }
"$py" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' \
  || { echo "bootstrap: $py is older than python 3.11" >&2; exit 1; }

# Serialize concurrent first-runs; the lock is held until this process exits (fd 9).
mkdir -p "$root"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$root/.bootstrap.lock"
  flock 9
fi

# Idempotent: the .ready marker means a prior run fully provisioned this venv. (A "dev"
# venv uses an editable install, so source edits are picked up live without reinstall.)
if [ -f "$venv/.ready" ]; then
  echo "$venv/bin/python"
  exit 0
fi

# Build in place (NOT build-in-tmp-then-mv): pip bakes the venv's absolute path into
# console-script shebangs (ansible-galaxy, …), so a move would leave them pointing at the
# old path. Clear any partial/failed build first; the flock above serializes runs, and the
# .ready marker — written only on success — makes a half-build non-idempotent.
rm -rf "$venv"
"$py" -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --disable-pip-version-check $install_spec
: > "$venv/.ready"

echo "$venv/bin/python"

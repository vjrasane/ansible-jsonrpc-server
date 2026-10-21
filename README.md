# ansible-jsonrpc-server

A JSON-RPC 2.0 server that executes Ansible over a stdio seam. It runs Ansible modules
and action plugins, gathers facts, and installs collections, exposing them as JSON-RPC
methods — so a client can drive Ansible without importing it. The server is transport-
and client-agnostic: anything that can spawn a process and speak the framing can use it.

## Install

```sh
pip install ansible-jsonrpc-server
```

Requires Python ≥ 3.11. Pulls in `ansible-core`, `msgspec`, and `pebble`.

## Run

```sh
python -m ansible_jsonrpc_server
# or the console script
ansible-jsonrpc-server
```

The process is meant to be **spawned by a client**, not used interactively: it reads
JSON-RPC requests on **stdin** and writes responses on **stdout**; all diagnostics go to
**stderr** (fd 1 is redirected to stderr internally so a stray `print` — even in a forked
worker — can't corrupt the frames). It exits on EOF or a `shutdown` notification.

Execution runs in a `pebble` fork pool: the parent process never imports Ansible, so it
stays responsive to control messages while module runs proceed concurrently in workers.

## Protocol

JSON-RPC 2.0 with **`Content-Length` framing** (LSP-style): each message is
`Content-Length: <n>\r\n\r\n` followed by `<n>` bytes of UTF-8 JSON.

### Methods

#### `runAction`

Run one module or action plugin on a host. If `collection` is given it is installed
first; if `host.gather_subset` is set, facts are gathered (and cached) first.

Params:

| Field | Type | Notes |
|---|---|---|
| `host` | `HostSpec` | target + connection (below) |
| `fqcn` | `string` | e.g. `ansible.builtin.copy` |
| `args` | `object` | module arguments (default `{}`) |
| `vars` | `object` | task vars, e.g. `ansible_python_interpreter` (default `{}`) |
| `flags` | `Flags` | `check_mode`, `diff`, `no_log`, `verbosity`, `timeout` |
| `collection` | `Collection?` | `{name, version, source?}`; omit for `ansible.builtin` |

Result — `ResultFrame`:

| Field | Type | Notes |
|---|---|---|
| `status` | `string` | `ok` \| `failed` \| `skipped` \| `unreachable` |
| `changed` | `bool` | |
| `result` | `object` | the module's own result dict (redacted when `no_log`) |
| `warnings` | `string[]` | |
| `deprecations` | `object[]` | |

#### `gatherFacts`

Gather facts for a host (via `ansible.builtin.setup`), cached per `(host.id, subset)`.
`host.gather_subset` selects the subset (e.g. `["min"]`, `["all"]`).

- Params: `{ host: HostSpec, vars?: object }`
- Result: the `ansible_facts` object (fact name → value).

### Shared shapes

```jsonc
HostSpec   { "connection": Connection, "id"?: string,
             "become"?: object, "gather_subset"?: string[] }
Connection { "type": "local" | "ssh", "vars"?: object }   // vars → ansible_host, ansible_user, …
Flags      { "check_mode": bool, "diff": bool, "no_log": bool,
             "verbosity": int, "timeout"?: int }           // timeout seconds; 0/null → none
Collection { "name": string, "version": string, "source"?: string }
```

`host.id` keys the per-host fact and interpreter caches; `host.become` is a var bag
(`ansible_become`, `ansible_become_user`, …). Unknown fields are ignored (forward-compatible).

### Control messages (client → server, notifications)

- `shutdown` — stop serving and exit.
- `$/cancelRequest` — `{ "id": <request id> }` cancels an in-flight `runAction`.

### Notifications (server → client)

- `progress` — internal activity not tied to a single reply, correlated by `token`:

```jsonc
{ "token": string, "phase": "begin" | "end",
  "kind": "collection" | "facts",
  "name"?: string, "version"?: string,   // kind=collection
  "host"?: string,  "subset"?: string[], // kind=facts
  "ok"?: bool }                           // on phase=end
```

### Errors

Standard JSON-RPC error objects: `-32601` method not found, `-32602` invalid params
(message carries the validation error), `-32000` handler failure (`data` carries the
Python traceback).

### Example

```jsonc
→ {"jsonrpc":"2.0","id":1,"method":"runAction",
   "params":{"host":{"connection":{"type":"local"}},"fqcn":"ansible.builtin.ping"}}

← {"jsonrpc":"2.0","id":1,"result":{"status":"ok","changed":false,
   "result":{"ping":"pong"},"warnings":[],"deprecations":[]}}
```

## Bootstrap script

Clients don't install the server by hand — they provision an isolated, version-keyed
virtualenv with a small POSIX-sh script kept in this repo and fetched at a release tag:

```
https://raw.githubusercontent.com/vjrasane/ansible-jsonrpc-server/v<version>/bootstrap/v1/bootstrap.sh
```

It is **version-agnostic** (the release is an argument, not baked in), so one script
serves every client and every version:

```sh
sh bootstrap.sh <version> [python]
# e.g.
sh bootstrap.sh 0.0.2
```

It creates `$XDG_DATA_HOME/ansible-jsonrpc-server/venv/<version>`, `pip install`s
`ansible-jsonrpc-server==<version>`, writes a `.ready` marker, and prints the venv's
python path on the last stdout line — the interpreter the client then spawns. It is
idempotent (a provisioned venv is a fast no-op) and serialized with `flock`.

For server development, set `ANSIBLE_JSONRPC_SERVER_SRC=<path>` to install that checkout
editable instead (keyed under a `dev` venv), so local edits are picked up live.

## Module introspection

The package also ships `introspect.py`, used by client code generators to derive typed
bindings from a collection's modules. It is a normal module in the installed package, so
it runs through the same provisioned venv:

```sh
python -m ansible_jsonrpc_server.introspect '<job-json>'
```

- Job: `{ "collection": "<ns.name>", "collectionsPath"?: "<dir>" }`. `collectionsPath`
  (an `ANSIBLE_COLLECTIONS_PATH` root) exposes a downloaded collection to the loader;
  omit it for `ansible.builtin`.
- Output (stdout, JSON): `{ "modules": [ { "fqcn", "style", "meta", "options",
  "returndocs" }, … ] }` — one record per module, with its documented argument and
  return types. No module source is extracted; the server builds execution payloads itself.

## License

GPL-3.0-or-later. This package depends on and embeds the execution model of `ansible-core`
(GPL). Running it as its own process behind a JSON-RPC seam is what lets a separately
licensed client drive Ansible without taking on that obligation.

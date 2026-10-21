# ansible-jsonrpc-server

A JSON-RPC 2.0 server that owns all Ansible execution behind a line-framed stdio seam.
It runs Ansible modules and action plugins, gathers facts, and installs collections on
behalf of a thin client, so the client never imports Ansible itself.

The server speaks JSON-RPC 2.0 over stdin/stdout with `Content-Length` framing. It is the
Python runtime behind [`@sensible-ts/core`](https://github.com/vjrasane/ansible-nonesense),
which provisions it into an isolated, version-keyed virtualenv and drives it over that seam.

## Install

```sh
pip install ansible-jsonrpc-server
```

## Run

```sh
python -m ansible_jsonrpc_server
# or the console script
ansible-jsonrpc-server
```

The process reads JSON-RPC requests on stdin and writes responses on stdout; diagnostics
go to stderr.

## License

GPL-3.0-or-later. This package depends on and embeds the execution model of
`ansible-core` (GPL); keeping it isolated in its own process is what lets MIT-licensed
clients drive it without taking on that obligation.

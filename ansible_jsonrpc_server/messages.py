"""Wire message types — the typed contract for the JSON-RPC seam (design §4.5).

Hand-written msgspec Structs now; the plan is to generate these (and the TS
client's mirror) from one neutral schema later, so this file is the single source
of truth for the shapes. Imports only msgspec, so it is safe in the ansible-free
parent. Unknown fields are ignored by `msgspec.convert` → forward-compatible with
newer clients; missing required fields raise `msgspec.ValidationError` at the seam.
"""

import msgspec


class Connection(msgspec.Struct):
    type: str
    vars: dict = msgspec.field(default_factory=dict)


class HostSpec(msgspec.Struct):
    connection: Connection
    id: str | None = None
    become: dict = msgspec.field(default_factory=dict)
    gather_subset: list[str] | None = None


class Flags(msgspec.Struct):
    check_mode: bool = False
    diff: bool = False
    no_log: bool = False
    verbosity: int = 0
    timeout: int | None = None      # seconds; None/0 → no task timeout


class Collection(msgspec.Struct):
    name: str                      # dotted "namespace.collection"
    version: str
    source: str | None = None      # galaxy server / Automation Hub; None → default galaxy


class RunParams(msgspec.Struct):
    host: HostSpec
    fqcn: str
    args: dict = msgspec.field(default_factory=dict)
    vars: dict = msgspec.field(default_factory=dict)
    flags: Flags = msgspec.field(default_factory=Flags)
    collection: Collection | None = None   # None → core/builtin, nothing to install


class GatherFactsParams(msgspec.Struct):
    host: HostSpec
    vars: dict = msgspec.field(default_factory=dict)


class ResultFrame(msgspec.Struct):
    status: str
    changed: bool
    result: dict
    warnings: list = msgspec.field(default_factory=list)
    deprecations: list = msgspec.field(default_factory=list)

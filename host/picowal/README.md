# Hosted PicoWAL

`picowal.pico` is the storage API used by PicoScript applications. It keeps
payload memory caller-owned (span handles), keeps file positions inside the
host, and exposes exact pack/card semantics:

```c
pw_open();
pw_close();
pw_put(pack, record, payload, create_only);
pw_get(pack, record);
pw_delete(pack, record);
pw_exists(pack, record);
pw_scan(pack, after_record);
pw_sync();
pw_recover();
```

`pw_get` returns a span handle or zero when absent. `pw_scan` returns the next
live record in ascending order, or `-1`; begin with `after_record=-1`. Mutation
statuses match the portable C PicoWAL oracle (`0=OK`, `1=INVALID`,
`2=NOT_FOUND`, `3=EXISTS`, `4=IO_ERROR`, and so on).

The API source is concatenated with an application before compilation. It only
uses public `Storage.*` hooks, so the same source runs under the Python and
browser reference VMs and under the native file host.

## Native file format

New files use `PWALHOST` format version 2. Integers are little-endian. Every
append record contains `(pack, record, length, CRC32, payload)`. CRC covers the
first three fields and payload. Open/recovery scans the valid prefix, rebuilds
the bounded in-memory index, and truncates a torn, truncated, oversized, or
bad-CRC tail. Version 1 files remain readable and retain their legacy record
layout; rebuilding them into a new image upgrades to version 2.

The file host is single-writer and serializes every storage hook. `Sync` is the
durability boundary. Sealed/read-only model-image support and derived overlays
remain separate from this authoritative card log.

## Tests

`tests/test_picowal_api.py` executes identical PicoScript operation traces in
the Python and JavaScript VMs (when Node is installed). The native
`test_storage_recovery.c` fixture verifies bad-CRC and torn-tail repair while
retaining the preceding logical database state.

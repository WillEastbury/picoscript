# Logging / tracing / auditing — the `Log.*` subsystem

## The question, and the honest answer it replaces

"Do we have a decent logging / tracing / auditing subsystem?" — a
background investigation found the honest answer was **no**: only
scattered, Python/JS-internal debug logs (`HostApi.log` / `PicoVM.log` in
`vm/picovm.js`), never exposed to scripts, used only as a diagnostic
convenience for unimplemented host-hook fallbacks and a couple of
side-channel events (`raise swirq`, DSP hardware fallback). Separately,
`Kernel.ProfileStart`/`ProfileEnd`/`TracePoint` are documented
(`docs/CONFORMANCE_LEVELS.md` L3) and have host-hook codes, but map to
`OP_NOOP` with **no runtime implementation anywhere** — aspirational, not
real. The C VM (`vm/picovm.c`) has no log/trace buffer at all. Card storage
(`picostore.py`, `host/picowal/storage_file.c`) is plain CRUD (or an
append-only file for durability) with no actor identity, timestamp, or
change-history concept — not an audit trail.

`Log.*` closes the "script-visible structured logging" part of this gap.

## What it is

A genuine, deterministic, script-visible, append-only log: any PicoScript
program (in any frontend — this is an ordinary host-call namespace, no
special grammar needed) can write leveled, message-bearing entries and read
them back, in order.

| Method | Signature | Effect |
|---|---|---|
| `Log.Write(level, messageSpan)` | `rs1=level rs2=span → rd=id` | Appends an entry; returns its sequence id (1-based). |
| `Log.Count()` | `→ rd=count` | Number of entries currently stored. |
| `Log.Level(id)` | `rs1=id → rd=level` | The entry's level (0 if `id` is unknown). |
| `Log.Message(id)` | `rs1=id → rd=span` | The entry's message span (0 if `id` is unknown). |
| `Log.Clear()` | `→ rd=1` | Discards all entries. |

```basic
DIM id = Log.Write(1, "user login failed")
DIM count = Log.Count()
DIM lvl = Log.Level(id)
Io.Write(Log.Message(id))
```

Implemented identically in the Python VM (`picoscript_vm.py`'s `_log_hook`)
and the JS interpretive VM (`vm/picovm.js`'s `_logHook`) — pure integer +
arena-span logic, so it's byte-identical on both, exactly like `Event.*`
(which this deliberately mirrors in shape/style).

## Why no timestamp

Entries are ordered by their sequence id, **not** a wall-clock timestamp.
This follows the VM's own established convention
(`docs/NAMESPACE_STATUS.md`): the VM has no clock, and any "now" value is
*not a pure function of its inputs* — it can't be computed deterministically
or parity-tested to a fixed value, so it must be host-injected (as
`DateTime.Now`/`Environment.GetSystemTime` already are), not baked into a
core VM primitive. A host wanting wall-clock-stamped logs can pair
`Log.Write` with its own `DateTime.Now()` call and store both, or correlate
entries by sequence id against its own external clock.

## What's still *not* covered (deliberately out of scope here)

- **`Kernel.ProfileStart`/`ProfileEnd`/`TracePoint`** remain unimplemented
  no-ops. A real profiling/tracing engine (recording call timings, a replay
  log for deterministic re-execution) is a materially different, larger
  feature than a structured log buffer — not attempted in this pass.
- **Storage/card audit trails** (who changed what, when, and the prior
  value) would need real versioning in `picostore.py` and/or
  `host/picowal/storage_file.c`'s append log — a separate, storage-layer
  feature, not a host-hook-namespace one.
- **Native C transpile / C VM** (`vm/picovm.c`, `lower_to_c`) does not
  implement `Log.*` yet. Unlike the exception engine's `laddr` (which is
  architecturally incompatible with the C transpile model and is explicitly
  rejected at compile time), `Log.*` is "just" more host-hook state (a
  Python/JS dict here; a small C array or linked list there) — there's no
  fundamental blocker. Because it's registered in `HOST_HOOK_CODES`, both
  `lower_to_c` and `lower_to_js` (native transpile) already emit a normal
  `pv_host2`/generic host call for it without any special-casing (same as
  most namespaces) — it compiles fine, but the underlying native/transpiled
  runtime has no implementation behind that call yet, so it's a silent
  no-op there, consistent with how several other documented-but-unbuilt
  namespaces (e.g. `Kernel.TracePoint`) already behave on that path. Not a
  new gap introduced here; just not yet closed.

## Testing

`tests/test_logging.py` covers: sequential id assignment + count tracking,
level/message readback, unknown-id reads returning 0, `Clear()`, determinism
(the same program run twice produces identical bytecode and output — no
hidden non-deterministic state), and Python/JS bytecode-VM parity for the
same compiled program.

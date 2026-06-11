# INV-25 structured debug trace — work for the PIOS build agent

**Audience:** the agent that builds the PIOS kernel / EL1 runtime (not the PicoScript
compiler or the standalone VMs).

INV-25 (`docs/INVARIANTS.md`): *"Debug trace is structured — every trap records source
span, IL op, bytecode pc, hook id, capsule id and binding id."*

## What is already built (compiler + VMs — do not redo)

The non-PIOS parts are implemented and parity-tested (`tests/test_debuginfo.py`):

- **Machine coordinates** — every fault carries `code` + `pc` + `detail`, byte-identical
  across the three VMs (`ctx->fault/fault_pc/fault_detail` in C; `PicoFault(code,pc,detail,
  message)` in Python; `Error.fault/.pc/.detail` in JS).
- **Debug table (symbol artifact)** — the compiler emits a side-band map
  `pc -> (src_off, op, ns, method)` via `lower_to_bytecode_safe(..., debug=<dict>)` /
  `lower_to_bytecode_with_debug` (Python) and `picoc.js` `compileWithDebug` (JS). It is
  byte-identical between the Python and JS compilers and **does not change the word
  stream** — it is a separate file, like a stripped binary + symbol file.
- **Symbolizer** — `symbolize(code, pc, detail, debug, source)` (Python `picoscript_il`,
  JS `picoc.js`) resolves a fault into a structured record:
  `{code, fault, pc, detail, op, target (= ns.method or op), off, line, col, source_line}`.
  Byte-identical across Python and JS. A fault raised by the Python VM **or** the portable
  C VM at the same pc symbolicates to the same record (off-device symbolication).

Design choice (respects the performance invariants): the embedded C runtime stays lean —
it emits only `pc`; the debug table is **not** loaded on-device. Symbolication happens off
the hot path. In production that off-device symbolizer is the **PIOS kernel**, which is the
only component that also knows the capsule and binding context.

## What the PIOS build agent must add (the EL1 part)

Two trap-record fields are PIOS runtime context and cannot be produced by the compiler or
the standalone VM:

1. **`capsule_id`** — the capsule/process that was executing when the trap fired. The
   scheduler/dispatcher knows this; stamp it into the trap record at the point the kernel
   observes a worker fault (alongside the existing `seq`/owner bookkeeping in
   `docs/PIOS_IO_BINDING.md`).
2. **`binding_id`** — the binding/hook the capsule was invoking when it trapped. For a
   capability or hook fault the hook id is already in the VM `detail` field and the debug
   table's `ns.method`; the kernel should map that to the concrete **bound** binding
   instance (the capsule's grant/lease record), which only the kernel holds.

### Required kernel work

- **Ship the debug table with the capsule image.** The agreed wire home is the INV-23
  module container (`pico_module.py` / `picovm.js` `packModule`; magic `0x50534331`).
  Add an **optional** debug section after the words: `[DEBUG_MAGIC, count, (pc, off,
  op_id, ns_id, method_id) * count]`. Keep it optional and skip-on-unknown so existing
  headerless word arrays and current modules still load (do not bump `ABI_VERSION` for a
  capsule that omits it). Mirror the pack/load in Python + JS first if you extend the
  container, and keep `tests/test_abi_version.py` green.
- **On-device (or host-side) symbolizer.** Reuse the exact field shape and algorithm of
  `symbolize()` (see `picoscript_il.symbolize` / `picoc.js` `symbolize`) so kernel traces
  match developer-tool traces byte-for-byte. Then extend the record with the two fields:

  ```
  {
    code, fault, pc, detail,        // from the VM fault (already produced)
    op, target, off, line, col, source_line,   // from symbolize() + debug table
    capsule_id,                     // ADD: kernel scheduler context
    binding_id                      // ADD: kernel binding/lease context
  }
  ```

- **Source for `line`/`col`/`source_line`.** These need the original source text. If the
  device cannot hold source, ship only `pc`+`off` in the trace and resolve `line/col/
  source_line` off-device from `off` + the source (the symbolizer already does this — pass
  `source=""` on-device to omit them, or pass the source to include them).

### Acceptance

- A kernel trap record for a faulting capsule contains all eight INV-25 fields.
- `op`, `target`, `off`, `line`, `col`, `source_line` are produced by the shared
  `symbolize()` logic (no divergence from the compiler/VM tooling — add a parity check
  mirroring `tests/test_debuginfo.py`).
- `capsule_id` and `binding_id` are filled from kernel context and are stable across a
  capsule's lifetime (generation-tagged per the descriptor invariants).

Until this lands, INV-25 is **enforced for source-span + IL-op** (compiler/VM, done) and
**pending for capsule/binding id** (this document).

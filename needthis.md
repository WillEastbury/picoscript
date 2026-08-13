# PicoScript dependencies required by application integrations

This is the handoff list for the PicoScript agent. It contains runtime
capabilities required by application integrations that are not yet specified
precisely enough for an implementation agent to rely on.

## P0 — required before application integration

### 1. Stable typed span/buffer ABI

Define and implement a portable ABI for binary spans:

- pointer/length/flags representation;
- borrowed versus owned lifetime;
- explicit copy/materialize operation;
- bounds-checked byte, 16-bit, 32-bit, and 64-bit access;
- little-endian pack/unpack helpers;
- empty-span and invalid-span semantics;
- maximum span length and allocation failure status.

Applications may use spans for token candidates, graph results, traces,
checkpoint pages, and serialized record batches.

### 2. Stable opaque handle lifecycle

Provide generation-checked handles for model, cursor, checkpoint, page, trace,
and provider objects. Each handle needs:

- type tag;
- generation counter;
- close/release operation;
- stale-handle detection;
- bounded table behavior;
- deterministic invalid-handle status.

### 3. Status and error ABI

Define one cross-runtime status table covering success, invalid argument, invalid
handle, malformed record, CRC failure, schema mismatch, checkpoint mismatch,
resource exhaustion, cancellation, provider unavailable, and unsupported
operation. `Status.Last` must be available and preserved until the next
fallible operation.

### 4. Atomic persistence primitives

Applications need provider-independent primitives for:

- temporary page creation;
- ordered writes;
- flush/sync;
- atomic commit marker;
- generation compare-and-swap;
- recovery of the last complete checkpoint;
- corruption quarantine.

### 5. Deterministic random/seed service

Provide a seeded deterministic RNG with explicit stream IDs so training,
sampling, shuffling, and tie-breaking can use independent reproducible streams.
The seed and stream position must be checkpointable.

### 6. 64-bit counters and offsets

Expose native-width or split-pair helpers for corpus positions, byte offsets,
record counts, training cursors, generation numbers, and file sizes. Arithmetic
must define overflow and comparison semantics on 32-bit targets.

## P1 — required for a complete implementation

### 7. Bounded cursor snapshot/copy API

Add typed cursor operations for copying the current record into caller-owned
arena memory, pinning a page during a view, and reporting invalidation after
advance/close/remap. Cursor limits and exhaustion behavior must be explicit.

### 8. Query IR builder and plan inspection

Expose a typed builder for predicates, projection, ordering, limits, MATCH,
PHRASE, NEAR, graph relation filters, signed-weight filters, and deterministic
plan inspection. Consumers need to know which index/scan path was selected.

### 9. Compressed page/record codec ABI

Expose the same bounded compression/decompression functions to PicoScript,
portable C, and JS, including required-output-size queries, corrupt-input
status, and no-allocation operation for embedded profiles.

### 10. Trace/event sink

Provide a bounded trace sink with event type, sequence, token position, node/edge
IDs, amplitude, status, and payload span. It must support clear, read-next,
drop-on-limit, and deterministic digest operations without contaminating model
state.

### 11. Cancellation and work-budget tokens

Every long operation needs a caller-owned budget containing maximum steps,
frontier expansions, records, bytes, and deadline/cancel state. Exhaustion must
return a resumable status where the operation contract permits it.

### 12. Schema/code-generation contract

Define how an application schema becomes identical field IDs/types/layouts in
Python, PicoScript, C, and JS. Include schema digest generation and a
compile-time failure for conflicting field definitions.

## P2 — required for production portability

### 13. Re-entrant context and scratch-arena API

Application inference and training contexts must be caller-owned and re-entrant. Add
explicit scratch arenas, marks, rewinds, nesting limits, and thread/worker
ownership rules; no hidden global semantic state.

### 14. Provider capability negotiation

Expose capability discovery for storage, compression, graph search, tensor/model
operations, persistence, and cancellation. Return a stable capability bitmap and
version so applications can choose a bounded fallback rather than probe by failure.

### 15. Cross-runtime parity harness

Provide a runner that executes one fixture through the Python reference,
PicoScript VM, portable C, and JS, comparing status, output spans, persisted
records, and trace digests.

### 16. Resource-profile registry

Define named profiles for host, browser, and PIOS with versioned limits for arena,
handles, cursors, graph frontier, postings, pages, checkpoints, and output.
Profiles must be serializable into checkpoints and reject incompatible loads.

## Explicit non-dependencies

The PicoScript runtime should not add application-specific graph propagation,
lexical rules, token policy, training schedules, loss functions, grammar policy,
or inference orchestration. Those belong in consuming applications and must use
the dependencies above.

## Acceptance criteria

Each dependency needs:

1. a documented signature and wire contract;
2. Python, JS, and portable-C behavior;
3. bounded failure semantics;
4. at least one conformance fixture;
5. no hidden global state;
6. deterministic behavior under a fixed seed/profile.

# PIOS I/O Binding — kernel integration work-order

A hand-off for the **PIOS agent**: wire `include/pios_io_binding.h` into the EL1
kernel so EL0 PicoScript capsules talk to the network/transport through the
descriptor + FIFO ABI. This is the executable plan; the contract it implements is
`docs/PIOS_IO_BINDING.md` (§0 invariants I1–I8, the `pooldesc`/`ctx_desc`/`pios_desc`
records, the FIFO message set, `port_cfg`). The EL0 half already exists and is
*simulated* in `picoscript_vm.py` / `vm/picovm.js` (the `Req.*`/`Resp.*` host
hooks) — match that behaviour exactly so the simulator stays a faithful model.

> **Scope note for the agent:** this repo (picoscript) has no kernel source.
> Vendor `include/pios_io_binding.h` into the PIOS kernel tree and implement the
> phases below against the *real* kernel structures (RP1/ETH RX, TLS, TCP,
> the FIFO/mailbox IPC, arena allocator, EL0 capsule scheduler). Where a kernel
> primitive already exists (e.g. the mailbox/return-FIFO model, arena alloc),
> bind to it rather than reinventing.

## Ground rules (do not violate — these are the acceptance criteria)

| | Invariant | What the kernel must do |
|---|-----------|--------------------------|
| **I1** | Kernel is sole authority on message boundaries | Parse only request-line + headers; fix CL/TE framing kernel-side; hand EL0 a **length-bounded** body it cannot read past. |
| **I2** | One owner per descriptor | `pooldesc.owner ∈ {FREE,THREAD,KERNEL}`; transfer, never share. |
| **I3** | Sealed graph immutable | After `RESP_SEAL`, reject further preamble/header descriptors; stream mode only *appends* body. |
| **I4** | Worker accesses only leased memory | Every EL0 read goes through a validated `pooldesc`; revoke faults it. |
| **I5** | Cross-capsule only via kernel FIFO | Descriptors move solely as `pios_fifo_message`s; even `ipc` is kernel-mediated. |
| **I6** | Binding enforces protocol legality | Kernel enforces framing/phase order/seal immutability/`reorder_mode`; EL0 expresses intent only. |
| **I7** | No reorder across a phase boundary | Honour `reorder_mode` (strict/bounded/all); coalesce only within a phase; never cross `FLUSH`/`UPGRADE`. |
| **I8** | Every descriptor eventually ACKed/revoked/released | `RESP_SENT` releases outbound; scope-exit / `LEASE_REVOKE` releases inbound; no leaks. |

## Phase 1 — pool + lease layer  (foundation, I2/I4/I8)

1. Per-capsule **micro-pool** of `struct pooldesc` in that capsule's EL0-readable
   RAM; the kernel (which sees all EL0 RAM) allocates/leases from it. No hot-path
   malloc.
2. Lease ops: acquire (mark `USED`, `owner=THREAD`), validate (cheap generation
   check — the `Lease.Validate/CachedValidate` semantics), revoke (set `REVOKED`;
   the next EL0 validate must fault → kernel synthesizes 503).
3. **Scope-bound release:** when a worker invocation returns / faults / is killed,
   sweep its `THREAD`-owned inbound descriptors → `RELEASED` → reclaim spans →
   recycle the connection.
4. **Conformance:** a pinned/slow worker can hold RX buffers only for its own
   invocation (I4); no descriptor is leaked (I8).

## Phase 2 — inbound: parse → bind → lease → deliver  (I1/I3-prep)

1. RX: NIC/DMA → TLS record decode → TCP reassembly (existing kernel paths).
2. Parse **request-line + headers only**; extract cookies/auth → resolve + **bind
   the principal** (kernel-authoritative; per-request, not per-connection).
3. Determine body framing from CL/TE → the kernel is the **single boundary
   authority** (I1). Small body ⇒ inline span pooldescs; large/unknown ⇒ a pull
   cursor.
4. Stamp a connection-scoped **seq/stream id**; fill a `pios_ctx_desc`; lease the
   inbound descriptors; post **`CTX_READY`** to the capsule FIFO; schedule the
   worker.

## Phase 3 — the FIFO message loop  (I5)

Implement handlers for `pios_fifo_message` (`pios_fifo_msg` tag):

- **K→C:** `CTX_READY` (deliver context), `BODY_CHUNK` (answer a pull), `RESP_SENT`
  (TX complete → release outbound), `LEASE_REVOKE` (reclaim inbound under pressure).
- **C→K:** `BODY_PULL` (stream-mode body), `RESP_SEAL` (commit preamble+headers —
  freeze them, I3), `RESP_WRITE` (append body), `RESP_END` (complete), `RESP_FAULT`
  (abandon → discard graph; synthesize 500 only if pre-first-flush).

All descriptor movement is messages — never a shared pointer hop (I5).

## Phase 4 — outbound: order → frame → transmit → release  (I3/I6/I7/I8)

1. Collect the EL0 **typed response graph** (`pios_desc`, kinds
   `PREAMBLE/HEADER/BODY/TRAILER/CONTROL/COMMIT/ABORT/UPGRADE`).
2. Order by **phase** per the port's `reorder_mode`
   (`strict|bounded|all`); coalesce/DMA-map only **within** a phase; never cross a
   `FLUSH`/`UPGRADE` boundary (I7). Deferred status ⇒ buffer + reorder so the
   preamble goes first.
3. **Framing (kernel owns it):** all body known before `COMMIT` ⇒ `Content-Length`;
   else chunked `TE`. Apply transform/compression and range policy per
   `PIOS_IO_BINDING.md` §4.1/§4.2 (transform before length finalization; `range_mode`
   slices/synthesizes 206/multipart/416). EL0 never writes chunk/multipart framing.
4. Place by **seq/stream id** (HTTP/1.1 in-order with a hold buffer; HTTP/2 by
   stream). On TX-complete, post **`RESP_SENT`** → release the outbound descriptors
   back to the pool (I8).

## Phase 5 — ports + binding kinds  (I6)

1. `pios_port_cfg` per listener: `reorder_mode`, `body_inline_max`, `default_kind`,
   plus TLS identity / principal-binding policy / timeouts / quotas.
2. Lifecycle per `binding_kind`: `unary` (respond / seal-write-end), `stream`
   (seal→write*→end), `duplex` (after `UPGRADE`, long-lived bidirectional FIFO peer,
   `reorder_mode` flips to strict), `datagram` (one inbound → optional reply),
   `ipc` (same records over an internal FIFO).

## Acceptance / bring-up

1. **Unary "hello":** an EL0 capsule that does `Resp.Status(200); Resp.Write("hi"); Resp.Respond()`
   serves a real `200 OK` over the bound port. Verify on Pi5 (RP1/ETH).
2. **Deferred status:** a handler that sets the status at `RESP_END` still emits it
   first on the wire (bounded-mode reorder).
3. **Streaming:** SSE/chunked endpoint flushes incrementally; a `FLUSH` control
   descriptor is a hard coalescing barrier.
4. **Fault paths:** pre-flush `RESP_FAULT` ⇒ clean 500; post-flush ⇒ connection
   teardown (you can't un-send a 200).
5. **Invariant sweep:** walk I1–I8 above as a checklist; the simulator
   (`tests/test_io_hooks.py`) is the reference for EL0-visible behaviour.

Keep the kernel's parsing surface minimal (headers + framing only); everything
else is EL0. The smaller the EL1 attack surface, the closer you are to I1/I6.

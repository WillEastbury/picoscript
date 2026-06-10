# PIOS I/O Binding — descriptor & FIFO ABI (EL0-facing contract)

How a PicoScript capsule (EL0) exchanges request/response data with the PIOS
kernel (EL1) without ever touching a socket, a TLS record, or HTTP framing. User
code expresses **application intent** (read the request, construct a response
graph); the kernel expresses **transport reality** (TCP, TLS, HTTP framing, DMA,
connection lifecycle).

This is the contract a worker can rely on. It is the realization of explicit design
decisions — each rule below cites the decision (`[D#]`) it comes from.

> **Status:** design spec. The kernel side lives in the PIOS repo; this document
> is the EL0/PicoScript-facing half of the same ABI.

---

## 0. Invariants

The ABI rests on eight invariants. Everything in §1–§9 is a *derivation* of these:
the kernel **enforces** them and a worker may **rely** on them. They are the
acceptance criteria for any implementation.

| # | Invariant | Enforced by |
|---|-----------|-------------|
| **I1** | The kernel is the sole authority on message boundaries. | kernel parses request-line + headers and fixes CL/TE framing; the worker gets a length-bounded body it physically cannot read past ⇒ request smuggling is impossible [§1, D1] |
| **I2** | A descriptor may have only one owner. | `pooldesc.owner ∈ {free, thread, kernel}`; ownership **moves** (thread → kernel at `seal`), never shared — linear / `iso` semantics [§2,§4, D3,D6] |
| **I3** | A sealed graph is immutable. | `seal` consumes the `iso` arena; committed preamble/headers are frozen and use-after-seal is a compile error. Stream mode **appends** new body descriptors, it never mutates sealed ones [§4, D2,D6] |
| **I4** | A worker may only access leased memory. | every read goes through a validated `pooldesc` lease; a revoked lease faults; there are no raw pointers outside leases [§2, D3] |
| **I5** | Descriptors may not cross capsule boundaries except through kernel-mediated FIFOs. | descriptors live in a capsule's micro-pool; movement is FIFO messages only — even `ipc` is kernel-mediated [§2,§6, D3,D5,D8] |
| **I6** | Protocol legality is enforced by the binding, not the worker. | the port + kernel enforce framing, phase order, `seal` immutability and `reorder_mode`; the worker only expresses intent and **cannot** emit illegal protocol [§1,§4,§5, D1,D5,D7] |
| **I7** | Reordering may never cross a phase boundary unless explicitly permitted by binding policy. | the core rule of §4; `reorder_mode` and range mode are the only openers [§4, D7] |
| **I8** | Every descriptor is eventually ACKed, revoked or released. | outbound: `RESP_SENT` ACK; inbound: worker-scope-exit release or `LEASE_REVOKE`; no leaks (liveness) [§2,§6, D3,D8] |

Grouped by what they buy: **I1** no smuggling · **I2+I3** linearity & immutability ·
**I4+I5** memory safety & isolation · **I6** kernel owns protocol reality ·
**I7** intent-preserving optimization · **I8** no leaks / liveness.

---

## 1. Privilege model (2-tier, minimal kernel surface) [D1]

```
  wire ─► NIC/DMA ─► TLS ─► TCP ─┐                      EL1 (kernel)
                                 │  parse request-line + HEADERS only
                                 │  → extract cookies/auth → BIND principal
                                 │  → determine body framing (CL/TE)  ← single
                                 │     authority on message boundaries
                                 ▼
                       ┌──────────────────────┐  FIFO   ┌────────────────────┐
                       │  bound context + body │ ──────► │  capsule worker     │  EL0
                       │  (leased pooldescs)    │ ◄────── │  (PicoScript)       │
                       └──────────────────────┘  resp   └────────────────────┘
```

The kernel parses **only** what identity and framing require: the request line
and headers. It does **not** parse the body — that is user mode. Because the
kernel already reads the headers, it also owns **message-boundary framing**
(`Content-Length` / `Transfer-Encoding`) and hands EL0 a *length-bounded* body.
One authority on where a request ends ⇒ request smuggling (CL.TE / TE.CL) is
structurally impossible: EL0 physically cannot read past the kernel's boundary.

Everything else — body-content parsing, routing, application logic, response
construction — is EL0.

---

## 2. The lease record: `pooldesc` [D3]

All EL0 access to request/response bytes is a **lease** on a typed span. The
lease record lives in the capsule's own micro-pool (the kernel sees all EL0 RAM,
so it leases directly into that pool — no hot-path `malloc`).

```c
struct pooldesc {           /* one lease, 16 bytes, pool-allocated */
    void    *ptr;           /* span base (kernel RX/TX arena, EL0-readable)   */
    uint32_t len;           /* span length — kernel-authoritative bound        */
    uint16_t kind;          /* descriptor kind (see §4) + flags                */
    uint8_t  owner;         /* 0 = free, 1 = thread (worker), 2 = kernel       */
    uint8_t  state;         /* bit0 used, bit1 released, bit2 revoked          */
};
```

- **Pool ownership** is at the **capsule** level (persists across invocations).
- A descriptor is marked **owned-by-thread** while a worker holds it.
- **Inbound** pooldescs auto-release on **worker scope exit** (return / fault /
  kill) → kernel reclaims spans → connection recycled.
- The kernel may **revoke** an inbound lease under memory pressure
  (`state |= revoked`); the next validated read faults → kernel synthesizes 503.
- A worker can therefore only pin RX buffers for the duration of *its own*
  invocation — slowloris / pin / use-after-free are gone by construction.

EL0 reads only through a validated lease (`Lease.Validate` / `Lease.CachedValidate`,
hooks 0x5A/0x5B); `CachedValidate` is a generation-counter compare after the first
validate, so steady-state cost is ~1 instruction per region.

---

## 3. Inbound: the bound context descriptor [D1,D3,D4,D8]

What a worker receives when it is invoked (delivered as a `CTX_READY` FIFO
message, §6):

```c
struct ctx_desc {
    uint32_t seq;             /* connection-scoped sequence / stream id [D8]   */
    uint16_t binding_kind;    /* unary | stream | duplex | datagram | ipc [D5] */
    uint16_t header_count;
    principal_t principal;    /* kernel-bound identity/capability [D1]         */
    pooldesc *headers;        /* parsed header table, as leased spans          */
    uint8_t   body_mode;      /* 0 = inline spans, 1 = pull cursor   [D4]      */
    union {
        struct { pooldesc *spans; uint16_t span_count; } inline_body; /* small */
        struct { uint32_t cursor; uint32_t hint_len;   } stream_body; /* large */
    } body;
};
```

- `principal` is **kernel-authoritative** [D1] — EL0 cannot forge it; it is bound
  per request (keep-alive may carry different principals per request, so it is a
  context field, never per-connection).
- **Small bodies** arrive **materialized** as `inline_body.spans` (fast path).
- **Large / unknown-length bodies** arrive as a **pull cursor**; the worker pulls
  more pooldescs as bytes arrive (`BODY_PULL` / `BODY_CHUNK`, §6) and can feed them
  straight into an incremental parser (a `dispatch`/`jmptab` state machine). [D4]

---

## 4. Outbound: the typed response descriptor graph [D2,D6,D7]

EL0 builds the response in an **`iso` (move-only) arena** as a graph of typed
descriptors. Every descriptor carries a **`kind`** tag and belongs to a **semantic
phase**; how aggressively the kernel may use them for wire ordering is a per-port
`reorder_mode` [D7], never a heuristic.

### Descriptor kinds

| kind            | phase     | notes                                                             |
|-----------------|-----------|-------------------------------------------------------------------|
| `DESC_PREAMBLE` | preamble  | status line: version / code / reason. May be set **last** [D2]; a response may carry an *informational* preamble (1xx) before the *final* one. |
| `DESC_HEADER`   | header    | response headers (or 1xx informational headers)                   |
| `DESC_BODY`     | body      | payload chunks — the only kind that streams                       |
| `DESC_TRAILER`  | trailer   | chunked / HTTP-2 trailers (metadata *after* body)                 |
| `DESC_CONTROL`  | in-band   | flow-control marker; carries a subtype (below); a coalescing barrier |
| `DESC_COMMIT`   | terminal  | seal/release marker — preamble + headers committed                |
| `DESC_ABORT`    | terminal  | discard the graph / emit an error (pre-flush) or tear down (post-flush) |
| `DESC_UPGRADE`  | boundary  | protocol switch — ordering policy flips `bounded → strict` after it |

`DESC_CONTROL` subtypes:

| subtype           | meaning                                                            |
|-------------------|-------------------------------------------------------------------|
| `FLUSH`           | push buffered body now; kernel **must not coalesce past it** (SSE / log streams) |
| `CHECKPOINT`      | a resumable / observable point in the stream                      |
| `END_STREAM`      | end of a stream body (HTTP-2 END_STREAM, SSE close)               |
| `CONTINUE_100`    | emit a `100 Continue` informational response                      |
| `EARLY_HINTS_103` | emit a `103 Early Hints` informational preamble + headers         |

### Phases and the core rule

An HTTP response flows through ordered semantic phases:

```
  [ info: 1xx preamble + header ]*  →  preamble  →  header  →  body  →  trailer  →  commit
                                                                              └─ upgrade ⇒ strict
```

**Core rule [D7]:** *reordering is allowed only **within** the same semantic
phase, never across a phase boundary — unless the binding policy explicitly
permits it.* The per-port `reorder_mode` sets the intra-phase freedom:

| reorder_mode | within a phase                       | across phases            |
|--------------|--------------------------------------|--------------------------|
| `strict`     | none — exact production order          | never                    |
| `bounded` (HTTP) | coalesce / compress / DMA-map / merge | never (phase order fixed) |
| `all`        | reorder + coalesce freely              | allowed                  |

So "can the kernel optimize without violating intent?" is precise and
configurable: `bounded` HTTP gets intra-phase optimization with **hard phase
boundaries**; `strict` gets nothing; `all` gets everything. A `FLUSH`
(`DESC_CONTROL`) is an additional barrier *even within* the body phase.

### `seal` ≠ complete [D2,D6]

`seal` (`DESC_COMMIT`) means "**preamble + headers are committed and immutable**" —
the point of no return for the status, **not** "the whole response is done":

- **Status committed early** (commit before first body flush) ⇒ kernel streams
  `BODY` as chunked `Transfer-Encoding`; the discard-and-500 fallback no longer
  applies (headers are on the wire), so `DESC_ABORT` here can only **tear down the
  connection**.
- **Status deferred** (commit at `end`, body buffered) ⇒ kernel emits
  preamble + headers + body in phase order; nothing is on the wire until commit, so
  the **discard-and-500 guarantee survives the whole response** and `DESC_ABORT`
  yields a clean error response.

Chunk framing is the kernel's call: all body descriptors known before commit ⇒
`Content-Length`; otherwise chunked `TE` (or a transform/compression forces
no-CL unless buffered — see §4.1). **EL0 never frames chunks.**

### 4.1 Body transform policy [D1,D7,D9]

Compression is modeled as a **body-phase transform policy**, not as a new
`DESC_CONTROL` marker. The policy has a per-port default and a per-response
hint/override on the response graph: `identity`, `negotiate`, or an allowed set
such as `{gzip, br}`. This keeps compression a kernel/DMA-stage concern over the
whole representation instead of an EL0-visible ordering event; a control marker
would imply a phase boundary EL0 must reason about, which is exactly what **I6**
avoids.

The kernel owns request headers (**I1/D1**), so it parses `Accept-Encoding`,
combines it with port policy and the response hint, chooses `identity` / `gzip` /
`br`, and sets `Content-Encoding` (and `Vary: Accept-Encoding`) as needed. EL0 may
say what transforms are acceptable for this response; EL0 never compresses bytes
itself. That preserves the descriptor graph as application intent, while making
compression an explicit exception to pure zero-copy for that body.

A transform changes the body length, so it runs **before** length finalization and
before the kernel chooses `Content-Length` vs streaming framing:

| transform applied? | full body known before `DESC_COMMIT`? | binding result |
|--------------------|----------------------------------------|----------------|
| no                 | yes                                    | emit `Content-Length` over the original body bytes |
| no                 | no                                     | stream with chunked `TE` (HTTP/1.1) / data frames |
| yes                | yes                                    | either buffer-transform-then-`Content-Length` if within quota, or stream transformed bytes with chunked `TE` |
| yes                | no                                     | stream transformed bytes with chunked `TE`; `Content-Length` is illegal |

If EL0 supplied a stale `Content-Length`, the binding canonicalizes or removes it;
EL0 cannot force an illegal combination (**I6**). The transform is an intra-`body`
stage, so it may coalesce or remap `DESC_BODY` spans only within that phase and
must not cross `FLUSH`, `trailer`, `commit`, or `upgrade` boundaries (**I7**).

### 4.2 Range mode policy [D1,D7,D10]

`range_mode` is an explicit port/binding policy (`off`, `single`, `multi`). When
it is enabled, the kernel parses `Range` / `If-Range` with the other request
headers (**I1/D1**) and validates ranges against the selected representation
length. EL0 still produces the **full** successful response body graph, or a
kernel-visible length for it; the kernel slices the body phase to the requested
offset span(s). This is the one sanctioned exception to "no cross-offset body
reorder", and it is legal only because `range_mode` opens it explicitly under
**I7**.

Range handling applies only to a successful `200` representation with known
length. If there is no usable length, the binding ignores `Range` and sends the
normal `200` response. If the range set is syntactically valid but unsatisfiable,
the kernel discards the body graph and emits `416 Range Not Satisfiable` with
`Content-Range: bytes */LEN`, then ACKs/releases descriptors normally (**I8**).

Single and multi-range responses are kernel-framed:

| range result | descriptor mapping |
|--------------|--------------------|
| one satisfiable span | final preamble becomes `206`; kernel emits `Content-Range: bytes A-B/LEN`, a `Content-Length` for the selected span when legal, and only the sliced `DESC_BODY` bytes |
| multiple satisfiable spans and `range_mode = multi` | final preamble becomes `206`; kernel synthesizes `multipart/byteranges` `Content-Type`, boundaries, per-part `Content-Range` headers, and body slices |
| multiple ranges with `range_mode = single` | kernel may coalesce overlapping/adjacent ranges into one span; otherwise it ignores `Range` and sends the normal `200` response rather than making EL0 frame multipart |

EL0 never writes multipart boundaries, just as it never writes chunk framing.
Those bytes are transport/binding reality, not application body intent (**I6**).

Range plus dynamic transform is deliberately narrow. RFC range semantics apply to
the selected representation bytes, including any content encoding; serving ranges
over a freshly compressed stream would require a complete encoded buffer or an
index. Therefore dynamic kernel transforms are disabled for ranged responses:
a satisfiable range forces `identity` unless the selected representation is
already pre-encoded with a known encoded length. Range wins over `negotiate`
because it preserves streaming and avoids making compression a hidden whole-body
buffering requirement.

### Ownership move at `seal` [D6]
`seal` **consumes** the `iso` response arena (linear/move semantics) → ownership
flips **thread → kernel** and any **use-after-seal is a compile error** in the
AOT PicoScript compiler. A runtime ownership flag (`pooldesc.owner = kernel`)
backstops any dynamically-assembled descriptors. The kernel holds the response
past worker scope until TX completes.

---

## 5. Binding kinds (one substrate, typed lifecycles) [D5]

All kinds share `pooldesc` + the descriptor records + the FIFO ABI; only the
**lifecycle contract** differs.

| kind       | transport            | lifecycle                                        |
|------------|----------------------|--------------------------------------------------|
| `unary`    | HTTP request/response| ctx → `respond()` (or `seal`/`write`/`end`)      |
| `stream`   | SSE, chunked, long-poll | ctx → `seal` → `write*` → `end`               |
| `duplex`   | WebSocket (post-upgrade) | ctx → long-lived **bidirectional FIFO peer**  |
| `datagram` | UDP                  | one inbound pooldesc → optional reply, no conn   |
| `ipc`      | capsule ↔ capsule    | same descriptor records over an internal FIFO    |

Implement `unary` + `stream` first; `duplex`/`datagram`/`ipc` add later **without
changing the descriptor ABI**.

### Port configuration (per-listener)
A **port** (listener) is configured once with the policies the kernel enforces
for every request it accepts — distinct from `binding_kind`, which is decided
**per request** (`ctx_desc.binding_kind`, e.g. an HTTP port that upgrades a given
request to `duplex`):

```c
struct port_cfg {
    uint16_t reorder_mode;     /* strict | bounded | all   [D7]                */
    uint32_t body_inline_max;  /* <= this many bytes ⇒ inline spans, else cursor [D4] */
    uint16_t default_kind;     /* binding kind for new connections [D5]        */
    uint16_t transform_policy; /* identity | negotiate | allowed-set default [D9] */
    uint8_t  range_mode;       /* off | single | multi [D10]                   */
    /* + TLS identity, principal-binding policy, timeouts, quotas …            */
};
```

HTTP ports run `reorder_mode = bounded`; a UDP port might run `all`; a binary
framed protocol that must not move bytes runs `strict`.

---

## 6. FIFO message format

Communication is FIFO-only and async (post to the kernel mailbox, completion
arrives on the return FIFO). Messages are fixed-size records; `seq` ties a message
to its request/stream.

**Kernel → capsule**
| msg            | payload                                             |
|----------------|-----------------------------------------------------|
| `CTX_READY`    | `ctx_desc` (§3) — a worker invocation begins         |
| `BODY_CHUNK`   | `seq, pooldesc, eof` — response to a `BODY_PULL`     |
| `RESP_SENT`    | `seq` — TX complete ⇒ **release** outbound descs [D8]|
| `LEASE_REVOKE` | `pooldesc` — reclaim an inbound lease under pressure |

**Capsule → kernel**
| msg            | payload                                             |
|----------------|-----------------------------------------------------|
| `BODY_PULL`    | `seq, cursor, max` — pull more body (stream mode)    |
| `RESP_SEAL`    | `seq, status?, header descs` — headers committed [D2]|
| `RESP_WRITE`   | `seq, body descs` — append body (may flush)          |
| `RESP_END`     | `seq, status?, trailer descs` — complete             |
| `RESP_FAULT`   | `seq` — abandon; kernel discards graph, 500 if pre-flush |

`respond(status, body)` is unary sugar = `RESP_SEAL` + `RESP_WRITE` + `RESP_END`
in one post. `RESP_SENT` releases the descriptors back to the capsule pool [D8].

---

## 7. The PicoScript-facing verbs

Request (read-only, lease-validated):

```c
Req.Seq()                 // connection-scoped sequence/stream id
Req.Principal()           // kernel-bound identity (cannot be forged)
Req.Header(nameSpan)      // -> value span (from the parsed header table)
Req.BodyMode()            // 0 = inline spans, 1 = pull cursor
Req.BodySpan(i)           // inline mode: i-th body span
Req.BodyPull(max)         // stream mode: pull next pooldesc (blocks via FIFO)
```

Response (build the `iso` graph, then a terminal verb):

```c
Resp.Status(code)         // DESC_PREAMBLE (status line); may be called last
Resp.Header(nameSpan, valueSpan)
Resp.Write(span)          // DESC_BODY
Resp.Trailer(nameSpan, valueSpan)
Resp.Seal()               // DESC_COMMIT: preamble+headers committed; iso arena moves
Resp.End()                // complete; ownership -> kernel
Resp.Respond(code)        // unary sugar: Seal + (pending Writes) + End

// control / phase descriptors (DESC_CONTROL subtypes + boundaries)
Resp.Flush()              // FLUSH: push body now, coalescing barrier (SSE/logs)
Resp.Continue()           // CONTINUE_100 informational
Resp.EarlyHints(...)      // EARLY_HINTS_103 informational preamble+headers
Resp.EndStream()          // END_STREAM (HTTP/2 / SSE close)
Resp.Upgrade(proto)       // DESC_UPGRADE: switch protocol; reorder flips -> strict
Resp.Abort(code)          // DESC_ABORT: pre-flush => clean error; post-flush => teardown
```

The original sketch becomes, unmodified in spirit:

```c
Resp.Write(s);
Resp.Write(": Request Complete");
Resp.Respond(200);        // 'commit' retired; status may even be set at the end
```

Streaming, with the status known up front:

```c
Resp.Status(200);
Resp.Header(ct, textplain);
Resp.Seal();              // kernel may begin chunked TX now
Resp.Write(chunk1);
Resp.Write(chunk2);
Resp.End();
```

Deferred status (decide the outcome after the work; full 500 safety):

```c
Resp.Write(part1);
Resp.Write(part2);
Resp.Status(ok ? 200 : 500);   // chosen at the end
Resp.End();                    // kernel buffers, emits in kind order
```

---

## 8. HTTP edge cases (HTTPD mode)

The phase model + control descriptors are what make `bounded` mode survive real
HTTP. Each oddity maps to an explicit mechanism — none is left to a heuristic:

| # | case | mechanism |
|---|------|-----------|
| 1 | **Trailers** (chunked / HTTP-2 metadata after body) | a real `trailer` phase after `body`; phase order `preamble → header → body → trailer`. |
| 2 | **HEAD requests** | request carries a `HEAD` flag; the kernel uses `DESC_BODY` descriptors **only for `Content-Length`** then **suppresses body TX**. EL0 writes the body normally and is oblivious. |
| 3 | **1xx informational** (`100 Continue`, `103 Early Hints`) | `DESC_CONTROL/CONTINUE_100` and `…/EARLY_HINTS_103` emit an **informational phase** before the final preamble. A response may have many info phases then exactly one final `preamble`. |
| 4 | **CONNECT / WebSocket / Upgrade** | a `DESC_UPGRADE` boundary descriptor; after it the port's `reorder_mode` flips `bounded → strict` and the binding becomes `duplex` — HTTP framing stops. |
| 5 | **Error after partial stream** | `seal` = *headers committed*, not *complete*. Pre-flush `DESC_ABORT` ⇒ clean error/500; **post-flush** `DESC_ABORT` can only **tear down the connection** (you cannot un-send a 200). |
| 6 | **Content-Length vs chunked** | binding policy + a descriptor flag: all `DESC_BODY` known before `DESC_COMMIT` ⇒ `Content-Length`; otherwise chunked `TE` / streaming; transform may force streaming unless buffered (§4.1). |
| 7 | **Header mutability boundary** | after `seal`, `preamble` + `header` descriptors are **immutable**; `body` still accepts `write` in `stream` mode, not in `unary` (already finalized). |
| 8 | **Compression / transform** | see §4.1: transform is a body-phase policy chosen by the kernel from `Accept-Encoding` + EL0 hints, and it runs before length/framing finalization. |
| 9 | **Range responses (`206`)** | see §4.2: explicit `range_mode` lets the kernel slice body spans, synthesize `206` / multipart framing, or emit `416`. |
| 10 | **Flush / low-latency** (SSE, logs) | `DESC_CONTROL/FLUSH` pushes buffered body immediately and is a **hard coalescing barrier** — the kernel must not merge body across it. `END_STREAM` closes the stream phase. |

**Invariant tying it together:** reordering/coalescing is permitted only *within*
one semantic phase and never across a phase boundary or a `FLUSH`/`UPGRADE` marker,
unless the binding policy (range mode, `reorder_mode = all`) explicitly opens it.


## 9. Properties this ABI guarantees

| Property                          | Invariant(s) · How                                  |
|-----------------------------------|-----------------------------------------------------|
| No request smuggling              | **I1** — kernel is the single message-boundary authority [D1]|
| No pin / slowloris / UAF on read  | **I4,I8** — scope-bound leases + kernel revoke + eventual release [D3]|
| Use-after-seal is a **compile** error | **I2,I3** — `iso` arena consumed at seal [D6]   |
| Deferred status keeps clean 500   | **I3,I6** — nothing on the wire until `end` [D2]    |
| Zero body copies (untransformed)  | **I4,I7** — leased spans in/out; coalesce intra-phase only; transform is an explicit opt-out [D3,D7,D9]|
| One substrate, honest lifecycles  | **I5,I6** — typed binding kinds over shared pooldesc+FIFO [D5]|
| No hot-path malloc / no leaks     | **I8** — capsule micro-pools, kernel-ACK release [D3,D8]|

---

## Appendix — decision log

| # | topic | decision |
|---|-------|----------|
| 1 | Privilege tiering | 2-tier, minimal kernel parsing surface (kernel reads request-line+headers, binds principal, owns body framing) |
| 2 | Response lifecycle | both unary + streaming; status/headers committed independently of body, deferrable to `end` |
| 3 | Span access | lease-gated via per-capsule micro-pools of `pooldesc`; scope-bound auto-release + kernel revoke |
| 4 | Request body | both: materialized spans (small) / pull cursor (large/streamed) |
| 5 | Binding kinds | typed kinds (unary/stream/duplex/datagram/ipc) over one shared substrate |
| 6 | Ownership move | layered: `iso` lease consumed at `seal` (compile-time) + runtime owner-flag backstop |
| 7 | Reorder policy | per-port `reorder_mode` (`strict`\|`bounded`\|`all`) over a typed **phased** descriptor graph (PREAMBLE/HEADER/BODY/TRAILER/CONTROL/COMMIT/ABORT/UPGRADE); reorder only within a phase, never across a boundary unless policy opens it |
| 8 | Write ordering | kernel-ACK FIFO release + kernel-assigned connection-scoped sequence/stream id |
| 9 | Transform policy | compression is a body-phase kernel policy (not an EL0 descriptor); dynamic transforms run before length finalization and may opt out of pure zero-copy |
| 10 | Range mode | `range_mode` is the explicit opener that lets the kernel slice body spans, synthesize `206` / multipart framing, and disables dynamic transforms for ranged responses |

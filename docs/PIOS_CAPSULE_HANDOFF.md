# PIOS Capsule Handoff — PicoScript ⇄ PIOS contract

**Audience:** the PIOS build agent. This is the stable contract to build the
capsule runtime against. **PicoScript** (this repo) provides the *language
surface*, a deterministic *manifest format + serializer*, the *server-entry
convention*, the *host-hook ABI*, and a *browser reference simulator*. **PIOS**
provides the *runtime*: real pack-aware storage, process launch, IO/FIFO
binding, principals, and resource enforcement.

The manifest format (§3), address format (§2), and server-entry contract (§4e)
are **frozen** for parallel work — implementations on either side build to these.

Related specs: `docs/PIOS_DEVICE_BINDINGS.md`, `docs/PIOS_IO_BINDING.md`,
`docs/PIOS_HOST_BINDINGS.md`, `docs/INV25_PIOS_TRACE.md`.

---

## 1. Capsule storage model

A **capsule is a pack namespace** (one pack = one capsule); the pack id lives in
the capsule range (default `1024..4095`).

Card layout is **manifest-declared** — **card 0 is authoritative**; the numbers
below are defaults a manifest may resize without a format break:

| cards | role |
|---|---|
| `0` | capsule manifest / index (format in §3) |
| `1..1000` | reserved system/runtime cards |
| `1001..10000` | source program cards |
| `10001..20000` | compiled bytecode cards |
| (declared range) | IPC FIFO descriptor cards |

**Program pairing (default convention):** program `N` source = `1000 + N`,
bytecode = `10000 + N`. Exposed as `Capsule.SourceFor(N)` / `Capsule.CodeFor(N)`.

```
pack 1024
  card 0      capsule manifest
  card 1001   source for process 1 (web)
  card 10001  bytecode for process 1
  card 1002   source for process 2 (api)
  card 10002  bytecode for process 2
```

---

## 2. Address format

- **Canonical:** `pack/card` in decimal — e.g. `1024/10001`.
- **Typed (equivalent, optional):** `capsule:1024/card:10001`.

The parser accepts both forms; the formatter always emits canonical `pack/card`.
`Card.Address(pack, card)` returns the canonical string; `Pack.Use(pack)` sets
the ambient pack so later `Card.Read/Write(card)` are pack-relative.

---

## 3. Canonical manifest format (card 0) — FROZEN

UTF-8, `\n` line endings, fully deterministic so card 0 is byte-stable.

**Header** (bare `key = value`, fixed order). Required: `capsule`, `name`,
`cards`. Optional, emitted only when set, in this order: `principal`, `mem_kib`,
`cpu_ms`, `fs`.

Then **one blank line**, then **process blocks** in declaration order, then
**ipc_fifo blocks** in declaration order. Blocks are separated by one blank line.
Block bodies are indented two spaces with keys in fixed order.

- `process` body order: `source`, `bytecode`, `io`, `entry`.
- `ipc_fifo` body order: `from`, `to`, `depth`, `frame_max`.

**Value rules:** names match `[A-Za-z0-9_-]+`; integers are decimal; `io` is
`tcp/<port>` or `fifo/<name>`; `cards` is `<lo>-<hi>`. The file ends with a
trailing newline.

```
capsule = on
name = demo
principal = app-user
mem_kib = 4096
cpu_ms = 1000
fs = /var/picowal/p1024
cards = 1001-20000

process = web
  source = 1001
  bytecode = 10001
  io = tcp/83
  entry = http

process = api
  source = 1002
  bytecode = 10002
  io = tcp/84
  entry = http

ipc_fifo = requests
  from = web
  to = api
  depth = 64
  frame_max = 1024
```

From this, PIOS maps:

```
tcp/83          -> bytecode card 10001 (process "web")
tcp/84          -> bytecode card 10002 (process "api")
fifo/requests   -> descriptor card/range (web -> api, depth 64, frame_max 1024)
```

**Binary alternative:** the same fields may also be stored as a PSC1 card
(`picoserializer` / `PicoBinarySerializer`, deterministic key-sorted encoding).
Canonical **text is primary** — it is the simplest cross-agent interchange and is
what `Capsule.Serialize` emits by default.

---

## 4. PicoScript-provided primitives (contract surface)

### 4a. Manifest builders — compile-time (emit the §3 manifest; no runtime bytecode)

High-level:
```
Capsule.Name("demo")          Capsule.Principal("app-user")
Capsule.MemoryKiB(4096)       Capsule.CpuMs(1000)
Capsule.Process("web")        Capsule.SourceCard(1001)   Capsule.BytecodeCard(10001)
Capsule.BindTcp(83)
Capsule.Fifo("requests", from="web", to="api", depth=64, frameMax=1024)
```
Lower-level equivalent:
```
Manifest.BeginCapsule("demo")
Manifest.Process("web", 1001, 10001)
Manifest.Bind("tcp", 83, "web")
Manifest.Fifo("requests", "web", "api", 64, 1024)
Manifest.End()
```
IO binding declarations (compile to manifest metadata, **not** runtime bytecode):
`Bind.Tcp(port)`, `Bind.Fifo(name)`, `Bind.Card(card)`.

Serializer: `Capsule.Serialize(manifest) -> bytes` (canonical §3 text),
`Capsule.Deserialize(bytes) -> manifest`.

### 4b. Pack/card runtime hooks (provider-backed)
`Pack.Use(pack)`; `Card.Read(card) -> span`; `Card.Write(card, span) -> ok`;
`Card.Address(pack, card) -> span`.

### 4c. Source/bytecode pairing helpers
`Capsule.SourceFor(N) -> 1000 + N`; `Capsule.CodeFor(N) -> 10000 + N`.

### 4d. IPC FIFO runtime hooks (provider-backed)
`Fifo.Create(name, depth, frameMax)` / `Fifo.Open(name) -> handle`;
`Fifo.Send(handle, span) -> ok`; `Fifo.Recv(handle) -> span`;
`Fifo.Poll(handle) -> count`. Descriptor records are declared in the manifest
(§4a `Capsule.Fifo`) and realised as descriptor cards.

### 4e. Server-entry contract — FROZEN
`Server.Main { ... }` (or `Http.Handle() { ... }`) marks a server-endpoint
program. The compiler guarantees **server-valid bytecode**: the program reads its
request via `Context.*`/`Req.*` and writes its response via `Net.Status` + body
(`Io.*`/`Resp.*`), honouring the INV-7 response-ownership rules. Minimal program:
```
Server.Main {
    Net.Status(200)
    Io.WriteByte(52)
    Io.WriteByte(50)
}
```
This compiles to the endpoint-shaped bytecode the port-82 worker already expects
(request-context setup → status → body).

### 4f. Debug / source map (already implemented — INV-25)
`lower_to_bytecode_with_debug` / `symbolize` (and `picoc.js compileWithDebug`)
produce `pc -> source offset`, `pc -> op id`, `pc -> namespace/method id`,
byte-identical Python↔JS. For capsule deployment this map is stored in a
**companion metadata card** alongside each bytecode card (off-device
symbolication keeps the runtime lean). See `docs/INV25_PIOS_TRACE.md`.

---

## 5. Readable authoring DSL

Capsule manifest (BASIC-style, lowers to §4a builders + emits the §3 card 0):
```
Capsule "demo" Pack 1024
Process "web" Source 1001 Bytecode 10001
Bind Tcp 83 To "web"
Fifo "requests" From "web" To "api" Depth 64 Frame 1024
```
Card data uses the **LOAD/STORE** split (reads vs writes) and devices use the
**GPIO** DSL — both lower to the existing `Storage.*` / `Gpio.*` hooks. See the
storage/device DSL section of the language docs.

---

## 6. Provides / implements split

| Concern | PicoScript (this repo) | PIOS (you) |
|---|---|---|
| Manifest format + builder/serializer (§3, §4a) | define + reference impl | parse card 0 |
| Address format parser/formatter (§2) | define + impl | use |
| Capsule/Manifest/Bind primitives (compile-time) | language + emit | consume manifest |
| Pack/Card/Fifo runtime hooks (§4b/§4d) | ABI + browser emulator | real driver + storage |
| Server-entry contract + compiler guarantee (§4e) | define + enforce | launch bytecode as scoped VM process |
| Debug/source map (§4f) | done (INV-25) | optional symbolication |
| Capsule storage backend | browser reference (in-mem/localStorage) | real WALFS / pack-aware store |
| Process launch, IO bind, principals, limits, scheduling | — | implement |

---

## 7. Deliverable checklist (PicoScript side)

Minimal-first-deliverable order requested by PIOS in **bold**:

1. **Pack/card address parser + formatter (§2)**
2. **Capsule manifest builder + deterministic serializer (§3, §4a)**
3. **Process entry declarations (§4a `Capsule.Process`)**
4. **IO binding declarations (§4a `Bind.*`)**
5. **IPC FIFO declaration records (§4a `Capsule.Fifo`)**
6. **Server-entry source convention/helper (§4e)**
7. Pack/Card/Fifo runtime hooks + `CAP_CAPSULE` + browser provider (§4b/§4d)
8. Readable capsule + LOAD/STORE + GPIO DSL (§5)
9. Browser `PiosCapsuleStore` reference provider (card layout, manifest card 0)
10. Debug-map companion-card convention (§4f)

Already shipped and importable today: the `Gpio.*` host-hook ABI + `CAP_GPIO`
(committed), and the INV-25 debug/source-map mechanism (§4f).

---

## 8. How PIOS imports this work

1. Read **card 0** and parse the manifest per **§3**.
2. Create a capsule runtime context for the pack.
3. For each `process`: load its `bytecode` card, bind its `io`, assign
   `principal`, enforce `mem_kib`/`cpu_ms`, and launch a VM-backed PicoScript
   process whose entry honours the **§4e** server contract.
4. Expose process-local host bindings scoped to the capsule.
5. Realise intra-capsule IPC FIFOs from the descriptor cards (§4d).

The browser `PiosCapsuleStore` reference (in this repo) demonstrates the exact
card layout and manifest round-trip; swap its storage backend for the real
WALFS/pack-aware store while keeping the **§3 manifest format** and **card
layout** unchanged.

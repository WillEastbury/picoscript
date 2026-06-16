# PicoScript Language & Runtime Specification (Draft v0.2)

Status: **Extended with v2 block-structured syntax and library namespaces**

This document defines PicoScript v1 (stable, namespace/method) and v2 (block-structured, case-insensitive) language models. Both target the same bytecode ISA.

PicoScript is a deterministic handler language for userland message processing in the Pico stack (PIOS kernel + picoweb runtime). It is not PIOS-only; a conforming host on any platform may compile and run PicoScript if it satisfies the runtime and ABI contracts defined here.

---

## 1. Scope and non-goals

PicoScript is a deterministic handler language for protocol and application logic. It is designed to run in a host runtime (PIOS/picoweb class host), not as a kernel or network stack replacement.

PicoScript **does not** own:

- socket creation/accept/connect
- TCP/UDP/IP semantics
- direct device I/O
- interrupt controller programming

PicoScript **does** own:

- bounded message/event processing
- deterministic transformation of input descriptors into output descriptors
- explicit state transitions over host-provided state
- arena allocation of its own process memory
- zero-copy descriptor/span shipping where possible
- lease-based access mediation using type hints + span/pointer(offset,length)

---

## 2. Language Versions

### v1: Namespace/Method Syntax (Stable, Frozen Bytecode)

Primary syntax: C#-style method calls on hardware namespaces.

```csharp
Storage.Load(tenant, pack, card, R0);
Math.Add(R1, R0, 42);
Flow.Branch(GT, R1, R0, :done);
```

**Properties:**
- Case-sensitive (`Storage.Load` ≠ `storage.load`)
- Statements end with `;` or newline
- Labels prefixed with `:` (`":done"`)
- No whitespace normalization (parsing is strict)
- Bytecode ISA v1 (stable, frozen at 16 opcodes)

**Namespaces (v1):** `Storage`, `Thread`, `Math`, `Flow`, `Dsp`, `Net`, `Kernel`, `Queue`, `Random`, `Memory`, `Span`, `Descriptor`, `Lease`

### v2: Block-Structured Syntax (New, Same Bytecode)

Alternative syntax: case-insensitive, BASIC-like with explicit block delimiters.

```basic
IF R0 EQ 42 THEN
    String.Concat(R1, R2, R3)
    Number.Format(R4, R3, 2)
ELSE
    Maths.Sqrt(R5, R6)
ENDIF

WHILE R9 LT 100
    Maths.Add(R9, R9, 1)
ENDWHILE

FOREACH item AS i IN items
    DateTime.GetNow(R7)
    Locale.Format(R8, R7, "en_US")
ENDFOREACH

SWITCH R0
    CASE 1
        Queue.Dequeue(R1, R2)
    CASE 2
    CASE 3
        Queue.Enqueue(R3, R4)
    ELSE
        Thread.Skip()
ENDSWITCH
```

**Properties:**
- Case-insensitive: `IF`/`if`/`If` all valid; `String.Concat`/`string.concat`/`STRING.CONCAT` all map to same opcode
- Whitespace-ignorant: comments (`//`), indentation, blank lines ignored
- Line endings: CRLF or LF (tracked for diagnostics)
- No semicolons or curly brackets required
- Explicit block delimiters: `IF/THEN/ELSEIF/ELSE/ENDIF`, `WHILE/ENDWHILE`, `DO/LOOP` (with `WHILE`/`UNTIL` at either end), `FOR/TO/STEP/NEXT`, `FOREACH/IN/ENDFOREACH`, `SWITCH/CASE/DEFAULT/ENDSWITCH`
- Loop control: `BREAK` (exit nearest loop or `SWITCH`), `SKIP` (continue nearest loop); also `GOTO label`, `GOSUB/SUB/ENDSUB`, `RETURN`
- Same v1 bytecode ISA (no new opcodes)

**Namespaces (v2 = v1 + Library):**
- **v1 core:** `Storage`, `Thread`, `Math`, `Flow`, `Dsp`, `Net`, `Kernel`, `Queue`, `Random`, `Memory`, `Span`, `Descriptor`, `Lease`
- **v2 new:** `String`, `Number`, `Maths`, `DateTime`, `Locale` (compile to host hooks like v1 extended namespaces)

### High-level frontends (implemented): C-syntax, BASIC, Python & English

Four high-level imperative frontends compile through a shared intermediate language
(**PicoIL**, `picoscript_il.py`) rather than mapping one statement to one
instruction. They introduce **named variables** (auto-allocated to `R0`–`R15` by a
loop-aware register allocator) and **integer expressions** with operator
precedence, then lower to the same frozen v1 bytecode — and also to C (`toC`) and
JavaScript (`toJS`).

All four are **case-insensitive for keywords and variable names**; `namespace.method`
resolves case-insensitively to the canonical host-ABI spelling. The Python-style and
English-style frontends **reuse the BASIC AST and lowerer verbatim** (only their
tokenizer/parser differ), so the same program in any of the four surfaces lowers to
**byte-for-byte identical bytecode** — asserted by the test suite.

**C-syntax frontend** (`picoscript_cfront.py`, `.pc`) — curly-brace, C-like:

```c
int total = 0;
for (i = 1; i <= 10; i++) {
    if (i % 3 == 0) { continue; }       // skip multiples of 3
    total += i;
}
int parity = (total % 2 == 0) ? 1 : 0;  // ternary
Net.Status(200);
print(total);
```

Supports: `int`/`var` declarations, assignment and compound assignment
(`+= -= *= /= %=`), arithmetic `+ - * / %`, comparisons, logical `&& || !`, the
ternary `?:`, pre/post `++`/`--`, `if/else`, `while`, `for`, `break`/`continue`,
`return`, `void` subroutines and calls, `print(...)`, and `Namespace.Method(...)`
host/Net/Storage calls.

**BASIC-like frontend** (`picoscript_basic.py`, `.pbas`) — block-structured:

```basic
DIM TOTAL = 0
FOR I = 1 TO 10
    IF I MOD 3 = 0 THEN
        SKIP                    ' continue to next iteration
    ENDIF
    TOTAL += I
    IF TOTAL > 20 THEN
        BREAK                   ' exit the loop
    ENDIF
NEXT
PRINT TOTAL

DO                              ' post-test loop: body runs at least once
    DEC TOTAL
LOOP UNTIL TOTAL <= 0
```

Supports: `DIM`/`LET` declaration and `x = expr` assignment (and `+= -= *= /=`),
`INC`/`DEC`, arithmetic `+ - * / MOD`, comparisons by symbol (`= <> < > <= >=`, where
`=` is equality inside a test) or word (`EQ NE LT GT LE GE`), logical `AND OR NOT`,
the `IIF(cond,a,b)` ternary, `IF/THEN/ELSEIF/ELSE/ENDIF`, `WHILE/ENDWHILE`,
`DO/LOOP` (`WHILE`/`UNTIL` guard at `DO` for pre-test or at `LOOP` for post-test),
`FOR/TO/STEP/NEXT`, `FOREACH/IN/ENDFOREACH`, `SWITCH/CASE/DEFAULT/ENDSWITCH`,
`GOTO`/labels, `GOSUB/SUB/ENDSUB`, `RETURN`, `BREAK`, `SKIP`, `PRINT`, and
`Namespace.Method(...)` calls.

**Python-style frontend** (`picoscript_python.py`, `.ppy`) — significant
indentation, colon blocks:

```python
total = 0
for i in range(1, 11):
    if i % 3 == 0:
        continue                # skip multiples of 3
    total += i
parity = 1 if total % 2 == 0 else 0   # conditional expression
print(total)
```

Supports: `x = expr` (first use declares) and augmented `+= -= *= /= %=`, arithmetic
`+ - * / %`, comparisons `== != < > <= >=`, logical `and`/`or`/`not`, `a if c else b`,
`if:`/`elif:`/`else:`, `while:`, the post-test `do:` … `until c` / `while c`,
`for i in range(n)` / `range(a, b[, step])`, `match x:` / `case N:` / `case _:`,
`goto L` + `label L`, `def name():` and `name()` calls,
`return`/`break`/`continue`/`pass`, `print(...)`, and `Namespace.Method(...)` host
calls. Line comments `#`.

**Natural-English frontend** (`picoscript_english.py`, `.eng`) — the *pièce de
résistance*: plain imperative sentences that compile to the very same bytecode (and,
through the C backend, to machine code). Compound statements use a colon + indented
block; simple statements end the line (a trailing `.` is idiomatic):

```text
Set total to 0.
For each i from 1 to 10:
    Increase total by i.
If total is greater than 50:
    Print total.
Otherwise:
    Print 0.
```

Statements: `Set X to …` / `Let X be …`, `Add … to X` / `Subtract … from X`,
`Increase/Decrease/Multiply/Divide X by …`, `Print/Show/Display …`, `If …:` /
`Otherwise if …:` / `Otherwise:`, `While …:` / `Repeat while …:` / `As long as …:`,
the post-test `Repeat:` … `Until c.` / `While c.`, `Repeat n times with X:` (0..n-1),
`For each X from a to b:` (a..b inclusive), `Choose x:` / `When v:` / `Otherwise:`
(switch), `Label name.` / `Go to name.`, the `a if c otherwise b` ternary,
`Define name:` / `To name:` and `Do name` / `Call name`, `Return` / `Stop` (break) /
`Skip` (continue), and bare `Ns.Method(a, b).` host calls. Comparisons read as words
(`is greater than`, `is at least`, `is`, `is not`, `exceeds`, …) joined by
`and`/`or`/`not`; arithmetic may be `plus`/`minus`/`times`/`divided by`/`modulo` or
the usual symbols. Line comments `#`.

The same compiler is ported to JavaScript (`vm/picoc.js`) so **all four frontends**
compile **in the browser**, byte-for-byte identical to the Python compiler; see
`docs/playground.html` and `docs/COMPILER_ARCHITECTURE.md`.

**Construct parity at a glance** — every construct exists in every frontend, spelled
idiomatically for its surface; equivalent programs lower to identical bytecode:

| Construct | C-syntax | BASIC | Python-style | Natural-English |
|-----------|----------|-------|--------------|-----------------|
| declare / assign | `int x = e;` | `DIM X = e` / `X = e` | `x = e` | `Set x to e.` |
| print | `print(x);` | `PRINT X` | `print(x)` | `Print x.` |
| if / else | `if (c) {…} else {…}` | `IF c THEN … ELSE … ENDIF` | `if c:` / `elif` / `else:` | `If c:` / `Otherwise if c:` / `Otherwise:` |
| while | `while (c) {…}` | `WHILE c … ENDWHILE` | `while c:` | `While c:` / `As long as c:` |
| do-loop (post-test) | `do {…} while (c);` | `DO … LOOP UNTIL c` | `do:` … `until c` | `Repeat:` … `Until c.` |
| counted for | `for (i=a; i<=b; i++) {…}` | `FOR I = a TO b … NEXT` | `for i in range(a, b+1):` | `For each i from a to b:` |
| index for (0..n-1) | `for (i=0; i<n; i++) {…}` | `FOREACH I IN n … ENDFOREACH` | `for i in range(n):` | `Repeat n times with i:` |
| switch | `switch (x) { case N: … break; default: … }` | `SWITCH x CASE N … DEFAULT … ENDSWITCH` | `match x:` / `case N:` / `case _:` | `Choose x:` / `When N:` / `Otherwise:` |
| dispatch (jump table) | `dispatch (x) { case N: … break; default: … }` | `DISPATCH x CASE N … DEFAULT … ENDDISPATCH` | `dispatch x:` / `case N:` / `case _:` | `Dispatch on x:` / `When N:` / `Otherwise:` |
| goto / label | `L: … goto L;` | `L: … GOTO L` | `label L` … `goto L` | `Label L.` … `Go to L.` |
| subroutine | `void f(){…}` / `f();` | `SUB f … ENDSUB` / `GOSUB f` | `def f():` / `f()` | `Define f:` / `Do f.` |
| break / continue | `break;` / `continue;` | `BREAK` / `SKIP` | `break` / `continue` | `Stop.` / `Skip.` |
| ternary | `c ? a : b` | `IIF(c, a, b)` | `a if c else b` | `a if c otherwise b` |
| inc / dec | `x++;` / `x--;` | `INC X` / `DEC X` | `x += 1` / `x -= 1` | `Increase x by 1.` / `Decrease x by 1.` |
| modulo | `x % 7` | `X MOD 7` | `x % 7` | `x modulo 7` |
| logical | `a && b \|\| !c` | `A AND B OR NOT C` | `a and b or not c` | `a and b or not c` |
| host call | `Net.Status(200);` | `NET.STATUS(200)` | `Net.Status(200)` | `Net.Status(200).` |


### Jump-table dispatch and the indexed jump

`dispatch` is a `switch` that compiles to a **real jump table** rather than a
compare chain — O(1) dispatch on a dense, non-negative integer selector. It is the
single primitive beneath `switch`, `match`, and event / hook / interrupt /
**protocol** dispatch, so a state machine (an in-PicoScript protocol parser, for
example) is expressed directly:

```c
state s = START;
while (running) {
    dispatch (s) {                 // O(1) indexed jump on the state
        case START:  …; s = HEADER; break;
        case HEADER: …; s = BODY;   break;
        case BODY:   …; s = DONE;   break;
        default:     s = ERROR;            // out-of-range selector
    }
}
```

It lowers to a jump table on **every** backend:

- **bytecode** — a bounds guard (selector into `[0, N)`, else default) then an
  **indexed jump**: `JUMP` with addressing mode `Rs2 = 0x3` means `PC = Rs1 + imm16`
  (selector + table base), landing on one of `N` inline `JUMP` table entries. This
  is a backward-compatible use of the existing addressing-mode field — the 16-opcode
  ISA is unchanged; an ordinary `JUMP` keeps `Rs2 = 0` (`PC = imm16`). `Rs2 = 0x1`
  is the pure-indirect form (`PC = Rs1`).
- **C / JS** (`toC` / `toJS`) — a native `switch`, which the host compiler emits as
  its own jump table for dense cases.

Cases are independent handlers and do **not** fall through (each ends by leaving the
dispatch), and an out-of-range selector always routes to `default` — important when
the selector comes from untrusted input.


### Interoperability

- v1 and v2 compile to identical bytecode for equivalent logic
- Bytecode does not indicate source language
- Editors can round-trip: load bytecode, display in either v1 or v2 syntax, re-save as bytecode (output matches input)

---

## 3. System contract (normative)

All I/O paths visible to PicoScript-hosted execution are restricted to:

1. kernel-shipped socket data descriptors via FIFO
2. RAM access
3. IPC FIFO
4. storage backend card/pack load

No other I/O classes are valid for conforming hosts.

## 4. Runtime architecture

### 4.1 Roles

- **Kernel (PIOS):** owns IRQ/SW_IRQ routing, sockets, FIFO transport, scheduling, wake/sleep decisions.
- **Host runtime:** drains inbound descriptors, invokes PicoScript entrypoints, enqueues outbound descriptors.
- **PicoScript program:** pure deterministic processing logic with bounded execution budgets.

### 4.2 Wake-drain-sleep lifecycle

1. Kernel signals data/work available (IRQ/SW_IRQ).
2. Worker wakes and drains inbound queue until empty (or budget cap).
3. Each descriptor dispatches into a PicoScript event entrypoint.
6. If inbound queue is empty, worker returns to wait state.

## 5. Bytecode VM contract

The bytecode ISA is stable and frozen. PicoScript compiles to 32-bit fixed instruction words:

- **Opcodes [31:28]:** 4-bit primary operation (OP_NOOP, OP_LOAD, OP_SAVE, OP_PIPE, OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_INC, OP_JUMP, OP_BRANCH, OP_CALL, OP_RETURN, OP_WAIT, OP_RAISE, OP_DSP)
- **Rd [27:24]:** destination register
- **Rs1 [23:20]:** source register 1
- **Rs2 [19:16]:** source register 2 or condition/addressing mode
- **Immediate [15:0]:** 16-bit immediate, card address, imm16, or sub-opcode

Reference opcode groups (v1 stable):

- storage/pipe: `LOAD`, `SAVE`, `PIPE`
- ALU: `ADD`, `SUB`, `MUL`, `DIV`, `INC`
- control flow: `JUMP`, `BRANCH`, `CALL`, `RETURN`
- thread: `WAIT`, `RAISE`
- DSP: `DSP` (with 16 sub-operations: MatMul, Softmax, Dot, etc.)
- hooks: `NOOP` + reserved imm16 range for host hooks (Kernel, Queue, Memory, Span, Descriptor, Lease, String, Number, Maths, DateTime, Locale)

## 6. Kernel/host ABI surface for queue processing

This ABI is the formal processing contract the language targets.

### 6.1 Descriptor-driven entrypoint

Host runtime invokes:

- `dispatch(program, event, context, opts)`

Where `context` contains:

- inbound payload descriptor/buffer
- caller-visible variable map
- optional metadata (connection/session/channel ids)

### 6.1.1 Core runtime constructs

Conforming hosts should provide these base constructs:

- `TypeHint` (logical data type classification)
- `Span` (`ptr`, `length`)
- `Descriptor` (`ptr`, `length`, `flags`)
- `Lease` (`lease_id`, `type_hint`, `span`, state/generation metadata)

Lease-first rule: script-visible access to spans/descriptors is mediated via leases, not raw pointer use.

### 6.2 Queue handling primitives (host ABI)

Host must provide operations equivalent to:

- `Q_DEQUEUE(in_q) -> descriptor|none`
- `Q_ENQUEUE(out_q, descriptor) -> ok|error`
- `Q_DEPTH(q) -> integer`

PicoScript code itself does not mutate kernel queues directly; it emits payloads and host maps emit output to outbound descriptors.

### 6.3 IPC and kernel FIFO calls

For conformance, the host must map script events and emits to kernel IPC/FIFO operations. The exact syscall/API naming is implementation-defined, but semantics are fixed:

- inbound descriptor source is kernel FIFO/IPC FIFO
- outbound descriptor sink is kernel FIFO/IPC FIFO
- ordering is FIFO within each queue
- descriptor ownership transfer is explicit at enqueue/dequeue boundaries

### 6.4 IRQ / SW_IRQ wait-fire semantics

Formal semantics:

- **WAIT_IRQ:** block worker until hardware IRQ indicates work available.
- **WAIT_SW_IRQ:** block worker until software interrupt indicates work available.
- **FIRE_SW_IRQ(target):** request wake/signal of target worker/process after enqueue.
- **SLEEP:** yield execution; host may map to wait-for-IRQ/SW_IRQ policy.

### 6.2 Language exposure and permission model

Language exposure:

- `SLEEP` is an execution opcode and maps to host-yield semantics.
- WAIT/FIRE remain host/kernel control-plane actions and should be exposed to script only through constrained builtins if needed by policy.

Permission model (normative):

- `FIRE_SW_IRQ(pid)` is a **request**, not a direct interrupt operation.
- The kernel/host must authorize the caller against policy (ACL/capability/ownership rules) before issuing the SW_IRQ set call.
- If authorization fails, no wake is fired and a permission error is returned.
- Implementations must not allow unprivileged cross-process wake requests.

### 6.3 Host hook primitives

Recommended host hook primitive surface (all namespaces compile to `NOOP` + reserved imm16 encoding):

- `Kernel.WaitIRQ([Rmask])` / `Kernel.WaitSWIRQ([Rmask])` for controlled wait operations.
- `Kernel.FireSWIRQ(Rpid)` for permission-gated wake requests.
- `Kernel.ProfileStart/ProfileEnd/TracePoint(...)` for profiling and deterministic event tracing.
- `Queue.Dequeue/Enqueue/Depth(...)` for per-item queue operations.
- `Queue.DequeueBatch/EnqueueBatch(...)` for amortized batching (10-100x throughput gain).
- `Random.U32(Rdest)` for host-backed random generation.
- `Memory.ArenaInit/Alloc/Reset/Stats(...)` for arena lifecycle and allocation control; `Memory.Set/Get(span, idx[, val])` for byte-addressable read/write into a span's backing arena.
- `Span.Make/Slice/Materialize/Len/Get(...)` for span construction and access: `Slice` returns a **zero-copy view** (shared backing bytes), `Materialize` **memcpys** to a fresh contiguous arena region (independent copy), `Len`/`Get` read length and elements.
- `Io.Write(span)` / `Io.WriteByte(reg)` for **string/byte output** — append a span's UTF-8 bytes (or one byte) to the output buffer that backs `print` and the HTTP body. `print`/`PRINT` of a numeric value pipes a 4-byte integer; `print` of a **string literal** lowers to a scratch-span build + `Io.Write`, so `print("hello")` works in every frontend. Decode the buffer as text with `vm.output_text()` (Python) / `vm.outputText()` (JS).
- **`Utf8Writer` / `Utf8Reader`** — arena-backed, no-allocation text/binary primitives. `Utf8Writer.New(ptr, cap) → w` opens a writer over a caller-owned arena region; `Byte/Int/Span(w, …)` append, `ToSpan(w) → span` / `Len(w)` / `Reset(w)` finish or rewind. `Utf8Reader.New(span) → r` scans: `Peek/Next(r)`, `Int(r)` (parse signed decimal), `SkipWs(r)`, `Match(r, byte)`, `Eof(r)`, `Pos(r)`. Per-byte ops touch only the arena — no runtime allocation on the hot path.
- **`Json.*`** — streaming JSON on a `Utf8Writer`, with automatic commas and string escaping: `BeginObject/EndObject/BeginArray/EndArray(w)`, `Key(w, nameSpan)`, `Str(w, span)`, `Int(w, n)`, `Bool(w, b)`, `Null(w)`, `Raw(w, span)`. Builds e.g. `{"status":"ok","count":42,"items":[1,2]}`.
- **`Xml.*`** — HTML/XML element writer on a `Utf8Writer`, with attribute and text escaping: `Open(w, tagSpan)`, `AttrName(w, nameSpan)`, `AttrValue(w, valSpan)`, `OpenEnd(w)`, `Text(w, span)`, `Close(w, tagSpan)`, `Empty(w)`. Builds e.g. `<a href="/x?a=1&amp;b=2">go &amp; see</a>`. String-literal arguments (`Json.Key("status")`, `Xml.Open("a")`) are staged into a scratch span automatically by every frontend.
- `Descriptor.Make/SetFlags/GetPtr/GetLen/GetFlags/CopyBatch(...)` for descriptor flow and bulk transfer.
- `Lease.Acquire/Release/Validate/CachedValidate/GetSpan/GetTypeHint(...)` for capability/lease-mediated access with fast-path validation.
- `Storage.GetSchemaForPack`, `Storage.SetSchemaForPack`, `Storage.AddCard`, `Storage.UpdateCard`, `Storage.DeleteCard`, `Storage.PatchCard`, `Storage.ReadCard`, `Storage.QueryCard` as backend-swappable storage API hooks. Cards are encoded with the **PicoBinarySerializer** (magic `PSC1`, self-describing, fields sorted by UTF-8 name bytes for determinism) and `QueryCard` accepts the card **query language** (`field OP value [AND|OR ...]`, `OP ∈ = == != <> < > <= >= ~`).
- **Program-level card CRUD/query** (executable in the reference + JS VMs, PicoStore-backed): a context model keeps every op within the 2-in/1-out host ABI. `Storage.UsePack(packId)` selects the pack; `Storage.AddCard() → id` creates an empty card and selects it; `Storage.EditCard(id) → id` selects an existing card; `Storage.SetField(nameSpan, intVal)` / `Storage.SetFieldStr(nameSpan, valSpan)` write a field; `Storage.GetField(nameSpan) → intVal` / `Storage.GetFieldStr(nameSpan) → span` read one; `Storage.DeleteCard(id) → ok`; `Storage.QueryCard(querySpan) → count` runs the query language and `Storage.QueryResult(idx) → id` iterates matches. C-style active-record sugar sits above this ABI: `Order ord = Storage.GetCard(pack,id)`, `ord.qty = 42`, `ord.qty--`, `Storage.SaveCard(ord)`, and `Storage.QueryCards(pack,"qty > 40")` lower deterministically to `UsePack/EditCard/GetField/SetField/QueryCard`. The low-level span/query API remains the schema-less/ordinal escape hatch.
- **Large-card slices:** blob/dataset cards must be accessed by range, not whole-card materialization. `Storage.SetSlice(offset,len)` selects a byte window, `Storage.CardLen(card)` returns a blob card length, `Storage.ReadSlice(card) → span` returns the selected window, and `Storage.WriteSlice(card, span) → ok` patches bytes at the current offset. The simulator keeps a deterministic blob-card backend; PIOS should back the same hooks with WALFS/SD range I/O for 400MB+ cards.
- **Inbound payload slices:** request, stream, and event data expose the same slice-first pattern. Whole-blob APIs remain (`Req.BodySpan`, `Stream.Span`, `Event.Data`); slice APIs are `Req.SetSlice/BodySlice/BodyLen`, `Stream.SetSlice/Slice`, and `Event.SetSlice/DataSlice/DataLen`.
- `String.Concat/Length/Substring/IndexOf/Split/Trim/ToUpper/ToLower/Replace/Format/Parse/Equals(...)` for string manipulation.
- `Number.Parse/Format/Round/Floor/Ceiling/Abs/Min/Max/Clamp/ToInt/ToFloat(...)` for numeric formatting and conversion.
- `Maths.Sqrt/Pow/Sin/Cos/Tan/Log/Exp/Abs/Min/Max/Gcd/Lcm(...)` for mathematical operations.
- `DateTime.GetNow/GetYear/GetMonth/GetDay/GetHour/GetMinute/GetSecond/ToTimestamp/FromTimestamp/AddDays/Format(...)` for datetime manipulation.
- `Locale.GetCurrent/SetCurrent/Format/Parse/GetLanguage/GetRegion/ToLocalTime(...)` for locale-aware formatting.
- `Environment.GetEnvVar/GetSystemTime/GetMemoryAvailable/GetCpuLoad/GetProcessId/GetHostname/GetTimezone/GetVersion/GetDebugMode(...)` for system state/info queries.
- `Context.GetUser/GetStoredRequest/GetScratchBucket/GetPermissions/GetHeaders(...)` (expensive, **lazy-decoded**) and `Context.GetPort/GetHost/GetVerb/GetPath/GetQueryString(...)` (cheap, cached) for request context.
- `Context.SetScratchValue/GetScratchValue(...)` for scratch bucket pass-through.
- `Crypto.Sha256/Sha512/Blake2b/Blake3/Sha1/Md5(...)` for **userland hashing** (stateless, hardware-accelerated, no secrets needed).
- `Crypto.HmacSha256/HmacSha512(...)` for **kernel-wrapped HMAC** (secure key material, audit logging).
- `Crypto.Sign/Verify/Encrypt/Decrypt(...)` for **kernel-wrapped asymmetric/symmetric crypto** (key material never exposed to userland).
- `Crypto.DigestInit/DigestUpdate/DigestFinal(...)` for incremental hashing of streaming/large data.
- `Thread.YieldCounted(iterations)` for preemption hint in tight loops.
- All host hook calls return explicit status/error codes via registers/flags; no silent fallback.

### 6.4 Lazy/On-Demand Context Decoding

Performance critical: avoid decoding all request metadata (user, permissions, headers, stored request) on every entry. Instead:

- **Expensive accessors (lazy-decoded):** `Context.GetUser()`, `Context.GetPermissions()`, `Context.GetHeaders()`, `Context.GetStoredRequest()` 
  - Only decode and deserialize when explicitly called
  - Host caches result per dispatch (first call pays cost, subsequent calls hit cache)
  - Returns lease/handle if data is large; script calls `Lease.GetSpan()` to materialize on demand

- **Cheap accessors (always available):** `Context.GetPort()`, `Context.GetHost()`, `Context.GetVerb()`, `Context.GetPath()`, `Context.GetQueryString()`, `Context.GetRemoteAddr()`, `Context.GetContentType()`, `Context.GetContentLength()`
  - Pre-decoded and in CPU registers
  - Return immediately without allocation/parsing

- **Scratch bucket:** `Context.SetScratchValue(key, value)`, `Context.GetScratchValue(key)`
  - Per-dispatch key-value store for passing state through call chain
  - Host provides storage; script manages keys/eviction

**Example (avoid middleware overhead):**

```basic
// Cheap path: most requests only need these
HTTP_VERB = Context.GetVerb()     // Cached register
HTTP_PATH = Context.GetPath()     // Cached register
REMOTE = Context.GetRemoteAddr()  // Cached register

IF HTTP_VERB EQ 200 THEN
    // Only if needed: decode expensive data
    USER_ID = Context.GetUser()   // Lazy decode (first call pays cost)
    PERMS = Context.GetPermissions() // Lazy decode
    ...
ENDIF
```

### 6.5 Cryptographic operations: userland vs kernel-wrapped

**Key management rule (security boundary):**

- **Userland keys (own, session, non-system):** script can execute crypto directly in userland. Fast path, no kernel overhead.
- **System keys (root, service, shared infrastructure):** script can only request crypto via kernel. Always kernel-mediated, FIFO-routed, audit-logged.

**Userland hashing and crypto with script-owned keys:**

PicoScript scripts can directly call stateless hashing and keyed operations when they own the key material:

- `Crypto.Sha256(Rdata_ptr, Rdata_len, Rhash_out)` — SHA-256 hash (hardware-accelerated, no secrets)
- `Crypto.Sha512(...)`, `Crypto.Blake2b(...)`, `Crypto.Blake3(...)` — modern hashes (hardware-accelerated)
- `Crypto.Sha1(...)`, `Crypto.Md5(...)` — legacy hashes (compatibility, hardware-accelerated)
- `Crypto.DigestInit/Update/Final(...)` — incremental hashing for streaming/large data

When script owns the key (e.g., session token, user password hash):

- `Crypto.HmacSha256(Rkey_ptr, Rkey_len, Rdata_ptr, Rdata_len, Rhmac_out)` — HMAC with script-owned key (fast path, direct execution)
- `Crypto.HmacSha512(...)` — script-owned HMAC

**Kernel-mediated keyed operations (system keys, audit trail):**

Scripts cannot directly access system cryptographic key material. All system key operations are kernel-routed via FIFO with audit logging:

- `Crypto.Sign(Rkey_handle, Rdata_ptr, Rdata_len, Rsig_out)` — asymmetric signature (kernel manages private key, FIFO-routed, audit-logged)
- `Crypto.Verify(Rcert_handle, Rdata_ptr, Rdata_len, Rsig_ptr, Rsig_len, Rresult_out)` — verification against kernel certificate store (FIFO-routed)
- `Crypto.Encrypt(Rkey_handle, Rdata_ptr, Rdata_len, Rcipher_out)` — symmetric encryption with system key (FIFO-routed, audit-logged)
- `Crypto.Decrypt(Rkey_handle, Rcipher_ptr, Rcipher_len, Rplain_out)` — symmetric decryption with system key (FIFO-routed, audit-logged)
- `Crypto.HmacSha256(Rkey_handle, Rdata_ptr, Rdata_len, Rhmac_out)` — HMAC with system key (FIFO-routed, audit-logged)
- `Crypto.HmacSha512(...)` — system key HMAC (FIFO-routed)

System key access:

- Script cannot directly create key handles; kernel provides them via context initialization or environment queries
- Key handles are opaque; raw key material is never exposed to userland
- All system key operations return status codes indicating success, permission error, or key not available

**Performance implications:**

- **Userland hashing:** O(1) latency, hardware-accelerated (AES-NI, SHA-256 extensions)
- **Userland HMAC with script key:** O(data_size) latency, hardware-accelerated
- **Kernel crypto (system keys):** IPC/FIFO round-trip latency (µs-ms range) + kernel context switch + audit logging (overhead ~100-1000µs depending on kernel queue depth)

**Security guarantees:**

1. **No system key leakage:** userland never sees raw system key material (only opaque handles)
2. **Audit trail:** kernel logs all system key operations (Sign, Verify, Encrypt, Decrypt, HmacSha256/512)
3. **Hardware acceleration:** both userland and kernel-mediated ops use CPU crypto extensions (AES-NI, SHA-256 extensions, etc.)
4. **Key rotation:** kernel manages lifecycle; scripts always get current valid keys via handles (transparent to script)
5. **Permission boundary:** scripts can request system crypto but kernel verifies authorization before granting access


## 7. Performance model

PicoScript includes optional performance hooks for amortization, profiling, and fast-path validation:

### 7.1 Batching & Amortization (Throughput)

- `Queue.DequeueBatch(count) → span` — drain multiple queue descriptors in one host call, amortizing wake-up overhead. Reduces per-item dispatch cost ~100x for bulk processing.
- `Queue.EnqueueBatch(span)` — enqueue multiple descriptors atomically. Preserves ordering and reduces context switches.
- `Descriptor.CopyBatch(src_span, dst_span, count)` — bulk span transfer for zero-copy forwarding.

Rationale: queue drains are hot; batching amortizes context switch cost. Expected throughput gain: 10-100x vs. per-item dispatch.

### 7.2 Fast-Path & Arena Heuristics (Latency)

- `Lease.CachedValidate(lease_id) → bool` — O(1) validation for hot leases (host caches generation on acquire). Typical host cache hit: ~5% of lease checks.
- `Memory.ArenaStats() → (total, free, fragmentation_pct)` — guide allocation policy without scanning arena. Used for heuristic pool rebalance decisions.
- `Thread.YieldCounted(iterations)` — hint that next N loop iterations should run before preemption. Allows tight loops to batch work and reduce preemption overhead.

### 7.3 Profiling & Diagnostics (Observability)

- `Kernel.ProfileStart(slot)` — begin timing bracket in named slot (host buffers timestamps).
- `Kernel.ProfileEnd(slot) → elapsed_ticks` — end bracket, return elapsed time for in-script decisions (e.g., early exit on timeout).
- `Kernel.TracePoint(event_id, data)` — emit tagged event for host trace/replay infrastructure. Deterministic and zero-cost if tracing disabled.

Rationale: identify bottlenecks without guessing. All profiling hooks are optional host bindings; absent implementations are NOOPs.

### 7.4 Determinism & performance tradeoff

- All performance hooks are **optional** and isolated from control flow. Omitting them preserves baseline determinism; using them enables optimization without specifying timing guarantees.
- Profiling is deterministic (same event sequence on replay) but does not guarantee cycle counts match across runs (host scheduler variance).
- Batching preserves queue ordering and descriptor integrity.

## 8. Determinism requirements

A conforming runtime must guarantee:

- bounded execution per dispatch (block/slice/output ceilings)
- deterministic control semantics (instruction behavior and budget enforcement)
- no hidden I/O side channels
- explicit error signaling on budget violation or invalid op

Recommended policy:

- disable unbounded host callbacks from script
- for deterministic-profile deployments, forbid wall-clock-dependent script logic
- allow random number generation where policy permits

Randomness policy:

- PicoScript may use RNG in userland logic.
- Host RNG seed material should combine:
  - system clock entropy
  - a random offset vector generated at host startup
- Conformance does not require deterministic value outputs across dispatches when RNG is enabled.

## 9. Security boundaries

- Kernel remains sole owner of network stack and privilege transitions.
- Script runtime is non-privileged and memory-bounded.
- Runtime access to spans/descriptors is lease-gated; lease validity/type hint checks are enforced by host/kernel policy.
- Queue descriptors are validated by host before script exposure.
- Script outputs are treated as untrusted until host validation passes.
- Profile/trace data is host-directed; scripts cannot read profiling state beyond their own `ProfileEnd` return value.

## 10. Compilation targets

PicoScript supports multiple execution targets, all from one shared IL (PicoIL):

1. **PicoScript bytecode VM** (default, deterministic runtime target), with three
   bit-compatible implementations:
   - Python reference — `picoscript_vm.py`
   - Portable C for bare metal (RP2354B/PIOS) — `vm/picovm.c` (freestanding-clean)
   - JavaScript for browser/Node with a step-debugging API — `vm/picovm.js`
2. **C emission** (`toC`) for native toolchain builds (Thumb/AArch64 via host toolchains) — `lower_to_c`
3. **JavaScript emission** (`toJS`) for direct browser/Node execution — `lower_to_js`
4. **C# emission** (`toCSharp`) for managed-host integration (planned)

Target choice must preserve deterministic contract and queue ABI semantics. A test
harness (`tests/test_pipeline.py`) asserts cross-target parity: the Python, C and JS
VMs produce identical register files, output bytes and HTTP status from the same
bytecode; emitted C/JS run and match; and the in-browser compiler's bytecode is
byte-for-byte identical to the Python compiler.

Compilation note: v1 (namespace/method), v2 (block-structured), and the four
high-level frontends (C-syntax, BASIC, Python-style, natural-English) all produce
bytecode for the same frozen 16-opcode
ISA. The high-level frontends lower through PicoIL (optimizer + loop-aware
register allocator); the in-browser compiler (`vm/picoc.js`) mirrors the Python
compiler exactly. Editors can round-trip register-level views (load bytecode,
display in a v1/v2 view, re-save; output matches input).

## 11. Conformance levels

- **L0 (Core):** parse/compile/disassemble + VM run/dispatch with deterministic budgets.
- **L1 (Queue host):** inbound queue drain + outbound queue emit integration.
- **L2 (Kernel-coupled):** IRQ/SW_IRQ wake-fire lifecycle integrated with FIFO ownership transfer.
- **L3 (Profiling & amortization):** optional performance hooks (batching, profiling, fast-path validation).
- **L4 (v2 syntax):** case-insensitive, block-structured source syntax + library namespaces (String, Number, Maths, DateTime, Locale). Includes the four high-level frontends — C-syntax, BASIC, Python-style and natural-English (named variables, expressions, structured control flow: `IF`, `WHILE`, `DO/LOOP`, `FOR`, `FOREACH`, `SWITCH`, `BREAK`/`SKIP`, `GOTO`/`GOSUB`) — lowering through PicoIL.
- **L5 (Context & environment):** lazy/on-demand context decoding (Environment.*, Context.* with cheap/expensive split + scratch bucket).
- **L6 (Cryptography):** userland hashing (hardware-accelerated) + kernel-wrapped keyed operations (HMAC, Sign, Verify, Encrypt, Decrypt) with audit logging and handle-based key access.

## 12. Open items for v0.3

- ~~v2 parser completion~~ (done: four high-level frontends — C-syntax, BASIC, Python-style, natural-English — via PicoIL); round-trip decompiler (bytecode → v2 syntax) still open
- fixed descriptor binary schema (header fields, endian, size limits)
- host hook namespace hardening and ABI freeze with crypto + context + library hooks
- formal memory model for shared RAM windows
- trace/event format for deterministic replay and audit
- profiling hook payload schema and buffer management
- v2 language completions and diagnostics in editor
- crypto key handle encoding and kernel key store interface
- `toCSharp` backend (toC and toJS implemented)
- register spill path for >16 simultaneously-live values (allocator currently errors)

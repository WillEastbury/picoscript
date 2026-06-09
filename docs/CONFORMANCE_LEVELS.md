# PicoScript Conformance Levels

**Conformance Levels** define what features are required/optional at different deployment tiers. Each tier is cumulative — L3 includes L0-L2 features.

## L0: Core VM (Required)

**What:** Basic parsing, compilation, bytecode execution with deterministic budgets.

**Includes:**
- Tokenization and parsing (v1 and v2 syntax)
- Bytecode compilation (source → bytecode)
- Bytecode disassembly
- VM execution with instruction-level budgets
- Register management (R0-R14)
- Control flow (jumps, branches, calls, returns)

**Hardware operations:**
- `Math.*` (Add, Sub, Mul, Div, Inc)
- `Flow.*` (Jump, Branch, Call, Return)
- `Dsp.*` (MatMul, Softmax, Dot, etc.)
- `Net.*` (HTTP response framing)

**Example:**
```
Flow.Call(:my_function)
Math.Add(R0, R1, 42)
Flow.Return()
```

---

## L1: Queue Host Interface (Recommended)

**What:** Inbound queue drain + outbound queue emit integration.

**Adds:**
- `Queue.Enqueue()` — Push results to kernel
- `Queue.Dequeue()` — Pull work items from kernel
- `Queue.Depth()` — Query queue depth
- Queue fairness and backpressure semantics

**Typical Use:**
Scripts receive work via `Dequeue()`, process, emit results via `Enqueue()`.

**Example:**
```
LET msg = Queue.Dequeue()
IF msg != null THEN
  // Process msg
  Queue.Enqueue(result)
ENDIF
```

---

## L2: Kernel-Coupled IRQ/SW_IRQ

**What:** IRQ/software interrupt wake-fire lifecycle integrated with FIFO ownership transfer.

**Adds:**
- `Kernel.WaitIRQ()` — Block until interrupt fires
- `Kernel.WaitSWIRQ()` — Block until software interrupt
- `Kernel.FireSWIRQ(pid)` — Permission-gated wake request

**Use Case:** Multi-process coordination, permission-based process waking.

**Example:**
```
Kernel.ProfileStart()
// Do work
Kernel.ProfileEnd()
Kernel.FireSWIRQ(worker_pid)  // Wake worker if permission granted
```

---

## L3: Profiling & Amortization (Optional)

**What:** Performance hooks for batching, profiling, and fast-path validation.

**Adds:**
- `Kernel.ProfileStart()`, `ProfileEnd()` — Measure code regions
- `Kernel.TracePoint()` — Emit trace events
- `Queue.DequeueBatch()`, `EnqueueBatch()` — Batch drain/fill
- `Descriptor.CopyBatch()` — Batch memory copy
- `Lease.CachedValidate()` — O(1) fast-path lease check
- `Thread.YieldCounted()` — Preemption hints

**Performance Notes:**
- 10-100x throughput improvement for batch operations
- No correctness impact if omitted

**Example:**
```
LET batch = Queue.DequeueBatch(100)
FOREACH item AS x IN batch THEN
  // Process x
ENDFOREACH
Descriptor.CopyBatch(batch)  // Batch transfer
```

---

## L4: v2 Syntax + Library Namespaces

**What:** Case-insensitive, block-structured source syntax + library functions.

**Adds v2 Syntax:**
- Case-insensitive keywords and identifiers
- Block-structured control flow: `IF/THEN/ELSE/ENDIF`, `WHILE/ENDWHILE`, `FOREACH/ENDFOREACH`, `SWITCH/CASE/ENDSWITCH`
- No semicolons, no curly braces, CRLF line endings
- `LET` variable declarations

**Adds Library Namespaces (host hooks):**
- `String.*` (12 methods) — Concat, Length, Substring, Split, Trim, ToUpper, ToLower, Replace, IndexOf, Format, Parse, Equals
- `Number.*` (11 methods) — Parse, Format, Round, Floor, Ceiling, Abs, Min, Max, Clamp, ToInt, ToFloat
- `Maths.*` (12 methods) — Sqrt, Pow, Sin, Cos, Tan, Log, Exp, Abs, Min, Max, Gcd, Lcm
- `DateTime.*` (11 methods) — GetNow, GetYear, GetMonth, GetDay, GetHour, GetMinute, GetSecond, ToTimestamp, FromTimestamp, AddDays, Format
- `Locale.*` (7 methods) — GetCurrent, SetCurrent, Format, Parse, GetLanguage, GetRegion, ToLocalTime

**Memory/Lease Primitives:**
- `Memory.*` (6 methods) — ArenaInit, ArenaAlloc, ArenaReset, ArenaStats, Peek, Poke
- `Span.*` (2 methods) — Make, Slice
- `Descriptor.*` (6 methods) — Make, SetFlags, GetPtr, GetLen, GetFlags, CopyBatch
- `Lease.*` (6 methods) — Acquire, Release, Validate, CachedValidate, GetSpan, GetTypeHint

**Example:**
```
LET first = "John"
LET last = "Doe"
LET full = String.Concat(first, " ", last)
LET len = String.Length(full)

IF len > 50 THEN
  LET short = String.Substring(full, 0, 50)
ENDIF
```

---

## L5: Context & Environment (Lazy Decoding)

**What:** Lazy/on-demand context decoding with cheap/expensive split + scratch bucket state passing.

**Adds Context (execution context):**
- **Cheap** (pre-cached, O(1)):
  - `Context.GetVerb()` — HTTP method
  - `Context.GetPort()` — Port number
  - `Context.GetHost()` — Hostname
  - `Context.GetPath()` — Request path
  - `Context.GetQueryString()` — Query string
  - `Context.GetRemoteAddr()` — Client IP

- **Expensive** (lazy-decoded, FIFO):
  - `Context.GetUser()` — User identity
  - `Context.GetPermissions()` — Access control list
  - `Context.GetHeaders()` — HTTP headers (large decode)

- **Scratch Bucket** (state passing):
  - `Context.SetScratchValue(key, value)` — Store value
  - `Context.GetScratchValue(key)` — Retrieve value
  - Passes state through call chain without middleware overhead

**Adds Environment (system info):**
- `Environment.GetEnvVar(name)` — Read env variable
- `Environment.GetSystemTime()` — Current timestamp (frozen at start for determinism)
- `Environment.GetMemoryAvailable()` — Free RAM
- `Environment.GetCpuLoad()` — CPU utilization
- `Environment.GetProcessId()` — Self PID
- `Environment.GetHostname()` — Machine hostname
- `Environment.GetTimezone()` — TZ offset
- `Environment.GetVersion()` — OS/runtime version
- `Environment.GetDebugMode()` — Debug flag

**Example:**
```
-- These are cached (fast):
LET verb = Context.GetVerb()
LET host = Context.GetHost()

-- These decode on first call, then cached:
LET user = Context.GetUser()        -- FIFO decode, expensive
LET perms = Context.GetPermissions()  -- FIFO decode, expensive

-- Scratch bucket for state passing:
Context.SetScratchValue("request_id", "req_12345")
// ... call another function ...
LET req_id = Context.GetScratchValue("request_id")
```

---

## L6: Cryptography (Userland + Kernel-Wrapped)

**What:** Userland hashing (hardware-accelerated) + kernel-wrapped keyed operations (audit-logged).

**Userland Hashing (Fast, no secrets):**
- `Crypto.Sha256(message)` — SHA-256 hash
- `Crypto.Sha512(message)` — SHA-512 hash
- `Crypto.Blake2b(message)` — BLAKE2b hash
- `Crypto.Blake3(message)` — BLAKE3 hash
- `Crypto.Sha1(message)` — SHA-1 hash (legacy)
- `Crypto.Md5(message)` — MD5 hash (legacy)
- `Crypto.DigestInit(algo)` — Start incremental hash
- `Crypto.DigestUpdate(handle, chunk)` — Add data
- `Crypto.DigestFinal(handle)` — Finalize hash

**Kernel-Wrapped Keyed Ops (Audit-logged, key protection):**
- `Crypto.HmacSha256(key_handle, message)` — HMAC-SHA256 with system key
- `Crypto.HmacSha512(key_handle, message)` — HMAC-SHA512 with system key
- `Crypto.Sign(key_handle, message)` — Sign with asymmetric key
- `Crypto.Verify(key_handle, message, signature)` — Verify signature
- `Crypto.Encrypt(key_handle, message)` — Encrypt with system key
- `Crypto.Decrypt(key_handle, ciphertext)` — Decrypt with system key

**Security Rule:**
- **Script-owned keys** (e.g., session tokens): Use `HmacSha256(my_key, data)` → Executes userland, fast
- **System keys**: Use `HmacSha256(kernel_handle, data)` → Kernel FIFO, audit-logged, key never exposed

**Example:**
```
-- Userland hash (no secrets):
LET sha = Crypto.Sha256("public message")

-- Script-owned HMAC (fast):
LET token = "user-session-abc123"
LET sig = Crypto.HmacSha256(token, "request body")

-- System key signing (audit-logged):
LET infrastructure_sig = Crypto.Sign(sys_key_handle, "audit log entry")
```

---

## Random Number Generation

**Conformance:** L3+

**Seeding:**
- RNG is seeded at host startup (not per-dispatch) from:
  - System clock entropy
  - Random offset vector generated at startup
- Scripts cannot control seed (no per-dispatch determinism override)

**Usage:**
```
LET random_value = Random.U32()  -- Returns 0-4294967295
```

---

## Storage Operations

**Conformance:** L5+

**Backend-Agnostic Card/Pack Operations:**
- `Storage.GetSchemaForPack(pack_id)` → Schema definition
- `Storage.SetSchemaForPack(pack_id, schema)` → Define schema
- `Storage.AddCard(pack_id, card_id, data)` → Insert record
- `Storage.UpdateCard(pack_id, card_id, data)` → Update record
- `Storage.DeleteCard(pack_id, card_id)` → Delete record
- `Storage.PatchCard(pack_id, card_id, patch)` → Partial update
- `Storage.ReadCard(pack_id, card_id)` → Read record
- `Storage.QueryCard(pack_id, query)` → Query records

**Backend:**
- Initially Picowal (embedded card store)
- Pluggable: S3, database, or custom storage

---

## Summary Table

| Level | Feature | When Required | Examples |
|-------|---------|---------------|----------|
| L0 | Core VM | Always | Parsing, bytecode, control flow |
| L1 | Queue I/O | Most deployments | Enqueue/Dequeue tasks |
| L2 | IRQ/SW_IRQ | Multi-process | Kernel.FireSWIRQ, wait/wake |
| L3 | Perf hooks | High throughput | Batching, profiling |
| L4 | v2 syntax + libs | Modern scripts | String, Number, Maths |
| L5 | Context + env | Web services | User, headers, env vars |
| L6 | Cryptography | Security-critical | HmacSha256, Sign, Verify |

---

**See Also:**
- LANGUAGE_SPEC.md — Full formal specification
- LANGUAGE_DOCS.html — Interactive documentation with examples
- METHOD_REFERENCE.html — Complete method reference with hook codes

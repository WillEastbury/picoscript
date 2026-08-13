# PicoScript Language Surfaces and Gaps

## Language-surface review`r`n`r`nPicoScript currently has seven language targets in the repository: C, BASIC, English, Python, COBOL, Report and Workflow. C/BASIC/English/Python are the primary shared-IL frontends; COBOL, Report and Workflow are additional or specialized surfaces that need independent grammar and parity assessment.

| Surface / layer | Supported features | Substrates / execution targets | Host hooks / integrations | Missing or incomplete items |
|---|---|---|---|---|
| C-style `.pc` | C-like declarations, expressions, arithmetic, comparisons, logical operators, ternary, control flow, subroutines, `const`, `enum`, `print`, namespace calls | PicoIL → bytecode, C, JavaScript | Registered namespaces including request/response, network, storage, memory, spans, blocks, bits and Dot8 | No first-class function values or ergonomic router abstraction; newer namespaces depend on host binding |
| BASIC-like `.pbas` | `DIM`, `LET`, assignments, compound assignments, `INC`/`DEC`, conditionals, loops, `FOREACH`, `SWITCH`, `BREAK`, `SKIP`, `GOTO`, `GOSUB`, `RETURN`, `PRINT`, `ENUM`, `CONST` | Same shared PicoIL and backends | Same ABI as C-style | Complex data structures remain primitive; no general collections or function-valued dispatch |
| Python-style `.ppy` | Significant indentation, colon blocks, Python-like expressions and control flow, named variables | BASIC AST/lowerer; bytecode, C, JS | Same host-hook surface | Deliberately a subset: no objects, imports, exceptions, comprehensions, generators or dynamic runtime |
| Natural-English `.eng` | Imperative sentence syntax, conditions, loops and host calls | English parser → BASIC AST → PicoIL → all backends | Same host-hook surface | Limited composability and diagnostics; not a general natural-language programming model |`r`n| COBOL `.cob` / COBOL-style frontend | COBOL-oriented declarations and imperative business logic where supported by `picoscript_cobol.py` | Specialized COBOL frontend; verify feature-by-feature against PicoIL and backends | Same namespace/host-hook ABI where calls lower successfully | Broader COBOL data divisions, file I/O, copybooks, condition names and legacy verbs need explicit conformance work |`r`n| Report frontend | Report-oriented declarative or tabular output surface around the report compiler/model modules | Specialized report compiler/model path | Report/output hooks and host data sources | Needs a clear grammar, source extension, lowering contract, pagination/layout semantics and parity tests |
| Workflow JSON | Visual-workflow step lists compiled through English → PicoIL | Same downstream targets | Same host hooks | Orchestration-oriented; limited rich branching/dataflow abstraction |
| v1 namespace syntax | Direct `Namespace.Method(...)` calls, labels, registers and explicit control flow; stable 16-opcode ISA | Python VM, C VM, JS VM, native C/JS | Kernel, queue, request/response, memory, span, descriptor, lease, storage, network, DSP and random hooks | Low-level and verbose; v1 is case-sensitive |
| v2 block syntax | Case-insensitive `IF`, `WHILE`, `DO/LOOP`, `FOR`, `FOREACH`, `SWITCH`, `BREAK`, `SKIP`, `GOTO`, `GOSUB`, `RETURN` | Same frozen bytecode ISA | Adds `String`, `Number`, `Maths`, `DateTime`, `Locale` | Specification is still draft/extended; full namespace parity must be maintained |
| Shared compiler / IL | Primary C/BASIC/English/Python frontends converge on PicoIL; optimization and loop-aware register allocation | Bytecode, native C, native JS | Generated hook names/codes | No complete self-hosting compiler; C# emission is planned |
| Python VM | Reference bytecode interpreter; cards, queries, spans, storage context and HTTP simulations | Hosted Python | `HostApi`, PicoStore, deterministic providers | Reference behavior is richer than the smallest native/PIOS host |
| JavaScript VM | Browser/Node VM, stepping/debugger and bytecode parity | Browser and Node | JS hook implementations and playground runtime | 32-bit JavaScript constraints limit exact parity for some systems cases |
| Portable C VM | Freestanding VM for Cortex-M33/RP2350/PIOS; native request/response and storage seams | Bare metal and hosted C | `pv_host_*`, storage, block, network and provider seams | Production PIOS kernel integration remains separate; several hooks are platform-dependent |
| Native C backend | Emits C for native/freestanding toolchains, including CAT-Q providers | Thumb, AArch64, Windows/POSIX/native host | Native block, HTTP, socket, host-provider and compute providers | Generated code still requires compatible host bindings |
| Native JS backend | Emits JavaScript to bypass the VM loop | Browser/Node | JS host-hook implementations | Host API and 32-bit numeric model constrain systems-level parity |
| Browser playground/site | Compile, run, step, inspect registers, disassembly, variables and output for all four styles | Browser | Inlined JS compiler/VM plus simulated HTTP/cards/query/spans | Simulator/debugger only; no real device, kernel, durable storage or interrupt integration |

## Substrate and host-hook grid

| Substrate | Current support | Host boundary | Missing items |
|---|---|---|---|
| Queue / events | Queue dequeue/enqueue, depth, batching, wake/sleep and SW IRQ model | `Kernel.*`, `Queue.*`, request/response hooks | Real PIOS FIFO/IRQ integration and policy enforcement in every deployment |
| HTTP / picoweb | Request parsing, response status/headers/body, streaming and native HTTP server support | `Req.*`, `Resp.*`, `Net.*`, context hooks | PicoScript does not own sockets, TCP, accept/connect or protocol stacks |
| Memory / arena | Arena allocation, reset, mark/rewind, peek/poke/set/get | `Memory.*`, `Arena.*`, `Io.*` | Larger address/window model; large in-script data remains awkward |
| Span / zero-copy data | Creation, slicing, materialization, length/get and lease-gated access | `Span.*`, `Lease.*`, `Descriptor.*` | Wider offsets and scalable large-card/blob handling |
| Storage / cards | PicoStore CRUD, schemas, active records, queries, exact-key operations, slices and WAL/recovery hooks | `Storage.*` | Real durable backend, production indexing, 64-bit keys and large-card support |
| Block storage | Block API and simulators; mmap on Windows/POSIX; WALFS LBA seam | `Block.*`, `pv_block_*` | PCIe/M.2/NVMe hardware path and complete PIOS integration |
| Search / relations | Hooks for lexical/vector/hybrid search, facets, query builders and graph relations | `Search.*`, `Query.*`, extended `Storage.*` | Production accelerated search/index backend; many methods are only ABI surface |
| Strings / numbers / templates | String operations, number formatting/parsing, template compile/render and UTF-8 reader/writer | `String.*`, `Number.*`, `Template.*` | More complete Unicode/locale behavior and consistent native/JS/PIOS implementations |
| Maths / time / locale | Math, random, date/time and formatting hooks | `Maths.*`, `DateTime.*`, `Locale.*`, `Random.*` | Platform-specific providers and deterministic policy for time/random |
| Crypto / security | Random bytes, crypto components, leases and capability model | `Crypto.*`, `X509.*`, `Lease.*` | Production key storage, TLS/X.509 integration and kernel authorization |
| JSON / XML / compression | JSON/XML streaming, Brotli and deterministic card serializer | `Json.*`, `Xml.*`, Brotli/PicoBinarySerializer | Optional codecs and host bindings remain incomplete |
| Bitwise / DSP / inference | `Bits.*`, `Dot8.*`, DSP, CAT-Q and ternary inference providers | `Bits.*`, `Dot8.*`, `Dsp.*`, `Tensor.*` | Broader hardware acceleration; 64-bit strategy and assembly nucleus remain open |
| Environment / scheduling | OS, CPU, memory, hostname, process/thread, elapsed-time, timers and scheduler hooks | `Environment.*`, `Timer.*`, `Scheduler.*`, `Thread.*` | Semantics vary by host; unsupported operations remain provider-specific |
| UART / SPI / I²C / PCIe | No generally available production driver substrate identified | No stable production `Uart.*`, `Spi.*`, `I2c.*` or `Pcie.*` surface | Concrete hardware-driver bindings are the largest practical gap |

## Overall assessment

PicoScript's language surfaces are comparatively mature: four source syntaxes, a shared IL, frozen bytecode, three VMs and native C/JavaScript lowering are present and substantially parity-tested.

The main incompleteness is below the language layer:

1. Host hooks are extensive, but many are contracts or simulators rather than universally backed facilities.
2. Real hardware integration is missing for UART, SPI/I²C, PCIe, NVMe/M.2 and related device paths.
3. Durable database capability is constrained by block-device integration, 64-bit keys, span/address limits and production indexing.
4. PIOS kernel integration—FIFO/IRQ, capability checks, descriptor validation and response lifecycle—remains deployment-specific.
5. The seven language targets have uneven status: C/BASIC/English/Python have the clearest shared-IL story, while COBOL, Report and Workflow need explicit specifications, feature matrices and cross-backend conformance tests.`r`n6. There is no full smoke-test suite that exercises all published intrinsics across all language surfaces and all major runtimes/backends; intrinsic coverage is still fragmented across targeted tests and substrate-specific suites.`r`n7. The language lacks higher-level abstractions such as first-class functions, route tables, collections and richer typed data structures.

In short: the compiler/runtime substrate is broad and usable; the production host and hardware substrate is the current frontier.



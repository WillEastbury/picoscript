# PicoScript

PicoScript is a small deterministic language for embedded and edge programs in
the Pico stack. It is designed for code that must run the same way in a browser,
on a host, and on bare metal.

> **Start here:** open the live portal at
> **https://willeastbury.github.io/picoscript/**, then read **Tutorial** and use
> the **Playground** tab. The portal lets you edit, compile, run, and step
> PicoScript in the browser.

## What you can do with it

- Write programs in **C-style**, **BASIC**, **Python-style**, or **English**.
- Compile all four styles through the same IL and runtime model.
- Run bytecode on the Python, JavaScript, or C VM.
- Transpile to native C or native JavaScript when you want to skip the VM loop.
- Use deterministic runtime namespaces for strings, numbers, templates, HTTP
  parsing, storage/cards, compression, hashing, events, and remote UI.
- Test parity: the same program should produce the same output on every path.

## Quick start

In the browser:

1. Open <https://willeastbury.github.io/picoscript/>.
2. Click **Tutorial** in the Reference docs, or open the **Guide** tab.
3. Click **Edit in Playground** on any guide card.
4. Click **Compile & Run**.

From a local checkout:

```powershell
cd C:\source\picoscript
C:\Python313\python.exe picoscript_build.py run examples\text_tools.pc --print
C:\Python313\python.exe picoscript_build.py emit examples\text_tools.pc --as bytecode --hex
```

The CLI prints VM output as packed integer chunks. The browser also shows a
decoded `text:` line when the output bytes are printable.

## Current state

- **Stable bytecode ISA:** 16 opcodes, deterministic host-hook model.
- **Four frontends:** C-style, BASIC, Python-style, and English all lower to the
  same PicoIL.
- **Five execution paths:** Python VM, JS VM, C VM, native C, and native JS.
- **In-browser portal:** the compiler and VM run directly from GitHub Pages.

GitHub mirror: https://github.com/WillEastbury/picoscript

## Scope

PicoScript runs inside picoweb/PIOS as deterministic, bounded userland logic for:
- Queue-driven message processing (FIFOs, IRQ/SW_IRQ wake events)
- Lease-based type-hinted span access to process memory
- Arena allocation and zero-copy descriptor shipping
- Optional batching, profiling, and fast-path validation hooks
- **Bitwise/shift (`Bits.*`)** and a **hardware-accelerated int8 dot product
  (`Dot8.*` → NEON SDOT / Cortex-M33 SMLAD / scalar)**, with `Memory.*`/`Io`
  compiling to a native byte arena — enough to run a full BitNet ternary
  inference forward pass in arena memory (see `docs/SYSTEMS_LANGUAGE.md`).

## Files

| File | Purpose |
|------|---------|
| `picoscript_lang.py` | v1 compiler & decompilers (stable) |
| `picoscript_lang_v2.py` | v2 tokenizer, parser, AST (case-insensitive, block-structured) |
| `picoscript_il.py` | **PicoIL** shared IR: optimizer, loop-aware register allocator, `lower_to_bytecode`, `lower_to_c`, `lower_to_js` |
| `picoscript_cfront.py` | **C-syntax** frontend (curly-brace; case-insensitive; lexer + Pratt parser → PicoIL) |
| `picoscript_basic.py` | **BASIC-like** frontend (block-structured, case-insensitive → PicoIL) |
| `picoscript_python.py` | **Python-style** frontend (significant indentation, colon blocks → reuses BASIC AST + Lowerer) |
| `picoscript_english.py` | **Natural-English** frontend (plain imperative sentences → reuses BASIC AST + Lowerer) |
| `picoscript_vm.py` | **PicoVM**: Python reference runtime for the 16-opcode ISA |
| `picoscript_build.py` | unified driver: source → `run` / `emit il\|bytecode\|c\|js` / `native` / `stats` |
| `picoscript_metrics.py` | IL/bytecode size, opcode histogram, static + (profiled) dynamic cycle estimates, C/JS backend sizes |
| `vm/picovm.h` `vm/picovm.c` | portable **C VM** for bare metal (RP2354B/PIOS); freestanding-clean. Native `Req.*`/`Resp.*`, `pv_storage_hook` |
| `vm/picovm_pool.c` `vm/picovm_pool.h` | thread-pooled **native HTTP server** runtime (accept loop, HTTP parse, per-worker arena). See `docs/NATIVE_HTTP_SERVER.md` |
| `vm/picovm.js` | **JS VM** for browser/Node debugging (step API; 32-bit parity) |
| `vm/picoc.js` | **In-browser compiler**: all four frontends → bytecode (byte-identical to Python) |
| `vm/pico_hooks.h` `vm/pico_hooks.js` | auto-generated host-hook codes (kept in sync with `picoscript_lang.py`) |
| `picoserializer.py` `vm/picoserializer.js` | **PicoBinarySerializer** for cards (magic `PSC1`, self-describing, deterministic field order); byte-identical Python/JS pair |
| `picostore.py` `vm/picostore.js` | **PicoStore**: pack CRUD (create/read/update/patch/delete/all) + **card query language** (`field OP value [AND\|OR ...]`); result-identical Python/JS pair |
| `docs/playground.html` | **Playground + language guide**: compile/run/step all four styles live in-browser |
| `gen_playground.py` | builds `docs/playground.html` from compiled, verified examples |
| `gen_site.py` | builds the consolidated GitHub Pages site `docs/index.html` (guide, playground, HTTP/TCP simulator, **Cards/Query/Spans** data engine, reference docs) |
| `tools/gen_dataset.py` | **synthetic dataset generator** for fine-tuning a small coding model: templated programs rendered to all 4 dialects, each **verified** (compiles + runs + identical output) → chat-format JSONL with train/val split. See [`data/README.md`](data/README.md) |
| `picoscript_runtime.py` | reference runtime structures (arena, lease manager, profiling, queue batching) |
| `picoscript.py` | ISA helpers and instruction encoding |
| `picoscript_opcodes.py` | Opcode reference |
| `LANGUAGE_SPEC.md` | Formal runtime and access model spec |
| `docs/TUTORIAL.md` | Followable getting-started path: portal, playground, CLI, spans, compression, UI, and parity checks |
| `docs/NAMED_CONSTANTS.md` | Standard enum/constant catalog, localization (`toLocale`), and user-defined `const`/`enum` syntax across all frontends |
| `docs/PRIMITIVES.md` | Maths, byte/span, string, encoding, crypto, RNG, compression, and streaming text primitive inventory |
| `docs/COMPILER_ARCHITECTURE.md` | Frontend/IL/backend lowering pipeline |
| `docs/picoscript-language-editor.md` | Language syntax, editor contract, completions |
| `docs/AGENT_PROMPT.md` | **Ready-to-use prompt** for an LLM/agent to generate valid PicoScript (grammar, rules, host calls, worked 4-style examples) |
| `docs/SELF_HOSTING.md` | Feasibility exploration: compiling PicoScript in PicoScript (staged bootstrap) + `examples/selfhost_emit.pc` PoC |
| `docs/PIOS_IO_BINDING.md` | EL0↔kernel descriptor + FIFO ABI: `pooldesc` leases, typed/phased response graph, binding kinds, response lifecycle (seal/write/end), HTTP edge cases |
| `docs/PIOS_IO_INTEGRATION.md` `include/pios_io_binding.h` | kernel-side **integration work-order** + standalone C ABI header (vendor into the PIOS kernel; phased plan with I1–I8 acceptance) |
| `docs/SYSTEMS_LANGUAGE.md` | feasibility + staged plan: **PicoScript as a systems language** — can it compile the PIOS kernel on itself? (toC bridge, primitive inventory, the 64-bit decision, the irreducible asm nucleus) |
| `docs/STRING_TEMPLATES.md` | arena **`String.*`** / **`Number.*`** primitives + the **`Template.*`** engine (AOT-compiled-at-save `{{hole}}` templates stored as walfs cards) |
| `docs/NAMESPACE_STATUS.md` | what's implemented vs the **hard reasons** the rest aren't self-contained VM primitives (external state / entropy / 64-bit-in-JS / scope) |
| `docs/picoscript-hardware.md` | Hardware bytecode contract |

## Toolchain: four frontends, one IL, three backends

All four surface languages lower to a shared intermediate language (**PicoIL**) and
from there to any execution target — the same ISA and queue ABI everywhere
(LANGUAGE_SPEC.md §10). The bytecode backend runs on three bit-compatible VMs
(Python, C, JavaScript). Every frontend is **case-insensitive** for keywords and
variable names, and they all build the **same AST** (the Python-style and
English-style frontends reuse the BASIC AST + Lowerer), so the same program in any
style lowers to **byte-for-byte identical bytecode**:

```
  C-syntax (.pc) ───┐                               ┌─→ bytecode → PicoVM (Python ref)
  BASIC (.pbas) ────┤                               ├─→ bytecode → picovm.c (bare metal)
  Python (.ppy) ────┼─→ AST ─→ PicoIL ─→ lower ─────┼─→ bytecode → picovm.js (browser/Node)
  English (.eng) ───┘         (opt + regalloc)      ├─→ C  (toC)  → host cc → Thumb/AArch64
                                                    └─→ JS (toJS) → browser / Node
```

The natural-English frontend means a program written as plain sentences
("`Set total to 0.` … `For each i from 1 to 10:` …") compiles, via the same
optimizer and register allocator, all the way down to **machine code** through the
C backend.

Lowering is the performance lever: the optimizer (const-fold, INC fusion,
dead-move removal) and a **loop-aware linear-scan register allocator** decide how
close to the metal the emitted shape is. The bytecode target is the most compact;
the C target hands straight-line code to the native toolchain; the JS target runs
directly in a browser.

### Quick start

```sh
# Run on the reference VM
python picoscript_build.py run   examples/sum.pc      --regs
python picoscript_build.py run   examples/fizzbuzz.pbas --print
python picoscript_build.py run   examples/sum.ppy     --print   # Python-style
python picoscript_build.py run   examples/sum.eng     --print   # natural English

# Inspect each stage
python picoscript_build.py emit  examples/sum.pc --as il
python picoscript_build.py emit  examples/sum.pc --as bytecode --hex
python picoscript_build.py emit  examples/fizzbuzz.pbas --as c  -o out.c
python picoscript_build.py emit  examples/sum.eng --as c  -o sum_from_english.c   # English -> machine code

# Native build (Thumb / AArch64 via zig cc)
python picoscript_build.py native examples/sum.pc --target aarch64-freestanding-none -o sum.o
```

The same four lines above (`.pc`, `.pbas`, `.ppy`, `.eng`) compile to **byte-for-byte
identical bytecode** — pick whichever surface reads best to you.

### Compile &amp; debug in the browser

`docs/playground.html` is a self-contained page (no server) showing **every
construct in all four styles side by side** (C-syntax, BASIC, Python-style and
natural-English). The whole compiler is ported to JS
(`vm/picoc.js`), so you can **type C-syntax, BASIC, Python-style or natural-English
source and compile it live** in the browser, then run and single-step it on the
inlined JS VM with full register/output/PC inspection. The GitHub Pages site
(`docs/index.html`, built by `gen_site.py`) embeds the **Monaco editor** with
syntax highlighting and `Namespace.Method` completion for all four dialects, plus a
step debugger that shows the disassembly (current PC highlighted) and **auto-watches**
(each named variable → its allocated register → live value) as you step. You can
also load a prebuilt example or paste bytecode hex from `emit --as bytecode --hex`.
Rebuild with `python gen_playground.py` / `python gen_site.py`.

The in-browser compiler is verified **byte-for-byte identical** to the Python
compiler (`tests/test_pipeline.py`), so what you debug in the browser is exactly
what runs on bare metal.

The portable C VM and emitted C both build freestanding for Cortex-M33 (Thumb)
and AArch64. Run `python tests/test_pipeline.py` to verify **cross-target parity**:
the Python, C and JS VMs produce identical register files, output bytes and HTTP
status from the same bytecode; emitted C/JS run and match the VM; and the
in-browser compiler's bytecode equals Python's.



## Language Versions

### v1: Namespace/method syntax (C#-style)

```csharp
Storage.Load(0, 1, 42, R0);
Math.Add(R1, R0, 42);
Flow.Branch(GT, R1, R0, :done);
Storage.Pipe(0, 1, 42, Stream.Out);
:done
Flow.Return();
```

**Features:**
- Case-sensitive
- Semicolons and colons for labels
- Stable v1 bytecode ISA (frozen)

### v2: Block-structured syntax (BASIC-like)

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

DO                          ' post-test: body always runs at least once
    Number.Decrement(R3)
LOOP UNTIL R3 EQ 0

DO WHILE R4 LT 8            ' pre-test form; UNTIL allowed at either end
    Maths.Add(R4, R4, 2)
LOOP

FOREACH item AS i IN items
    DateTime.GetNow(R7)
    Locale.Format(R8, R7, "en_US")
ENDFOREACH
```

**Features:**
- Case-insensitive (keywords, identifiers, namespaces, methods)
- Whitespace-ignorant (preserves CRLF for line tracking)
- No semicolons or curly brackets
- Explicit block delimiters: `IF/THEN/ELSE/ENDIF`, `WHILE/ENDWHILE`, `DO/LOOP` (pre- or post-test via `WHILE`/`UNTIL`), `FOR/NEXT`, `FOREACH/IN/ENDFOREACH`, `SWITCH/CASE/ENDSWITCH`
- `BREAK` exits the nearest loop or `SWITCH`; `SKIP` jumps to the next loop iteration (skipping an enclosing `SWITCH`)
- New library namespaces: `String.*`, `Number.*`, `Maths.*`, `DateTime.*`, `Locale.*`
- Same underlying bytecode ISA (v1 stable)

## Formal specification

- `LANGUAGE_SPEC.md` — runtime/access model/conformance levels, including queue handling, IRQ/SW_IRQ wake/sleep, lease-based access, performance hooks
- `picoscript_runtime.py` — reference host-side runtime structures for arena allocation, lease management, profiling, queue batching
- `docs/picoscript-language-editor.md` — language surface, editor contract, completions, diagnostics

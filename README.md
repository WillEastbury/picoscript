# PicoScript

Deterministic userland scripting language for embedded queue-driven processing in the Pico stack.

> **▶ Live playground &amp; reference:** open **[`docs/index.html`](docs/index.html)** (or the
> GitHub Pages site at **https://willeastbury.github.io/picoscript/**) to browse every
> language construct (C-style and BASIC side by side), compile/run/step programs in the
> browser, and send HTTP/TCP requests to a program over a localStorage-backed PicoWAL store.

## Current state

- **v1 (stable):** Case-sensitive, namespace/method syntax with semicolons. Fixed bytecode ISA.
- **v2 (new):** Case-insensitive, block-structured, CRLF line-ending syntax. Extended with String/Number/Maths/DateTime/Locale libraries.

GitHub mirror: https://github.com/WillEastbury/picoscript

## Scope

PicoScript runs inside picoweb as deterministic, bounded userland logic for:
- Queue-driven message processing (FIFOs, IRQ/SW_IRQ wake events)
- Lease-based type-hinted span access to process memory
- Arena allocation and zero-copy descriptor shipping
- Optional batching, profiling, and fast-path validation hooks

## Files

| File | Purpose |
|------|---------|
| `picoscript_lang.py` | v1 compiler & decompilers (stable) |
| `picoscript_lang_v2.py` | v2 tokenizer, parser, AST (case-insensitive, block-structured) |
| `picoscript_il.py` | **PicoIL** shared IR: optimizer, loop-aware register allocator, `lower_to_bytecode`, `lower_to_c`, `lower_to_js` |
| `picoscript_cfront.py` | **C-syntax** frontend (curly-brace; case-insensitive; lexer + Pratt parser → PicoIL) |
| `picoscript_basic.py` | **BASIC-like** frontend (block-structured, case-insensitive → PicoIL) |
| `picoscript_vm.py` | **PicoVM**: Python reference runtime for the 16-opcode ISA |
| `picoscript_build.py` | unified driver: source → `run` / `emit il\|bytecode\|c\|js` / `native` |
| `vm/picovm.h` `vm/picovm.c` | portable **C VM** for bare metal (RP2354B/PIOS); freestanding-clean |
| `vm/picovm.js` | **JS VM** for browser/Node debugging (step API; 32-bit parity) |
| `vm/picoc.js` | **In-browser compiler**: C-syntax & BASIC → bytecode (byte-identical to Python) |
| `vm/pico_hooks.h` `vm/pico_hooks.js` | auto-generated host-hook codes (kept in sync with `picoscript_lang.py`) |
| `picoserializer.py` `vm/picoserializer.js` | **PicoBinarySerializer** for cards (magic `PSC1`, self-describing, deterministic field order); byte-identical Python/JS pair |
| `picostore.py` `vm/picostore.js` | **PicoStore**: pack CRUD (create/read/update/patch/delete/all) + **card query language** (`field OP value [AND\|OR ...]`); result-identical Python/JS pair |
| `docs/playground.html` | **Playground + language guide**: compile/run/step both styles live in-browser |
| `gen_playground.py` | builds `docs/playground.html` from compiled, verified examples |
| `gen_site.py` | builds the consolidated GitHub Pages site `docs/index.html` (guide, playground, HTTP/TCP simulator, **Cards/Query/Spans** data engine, reference docs) |
| `picoscript_runtime.py` | reference runtime structures (arena, lease manager, profiling, queue batching) |
| `picoscript.py` | ISA helpers and instruction encoding |
| `picoscript_opcodes.py` | Opcode reference |
| `LANGUAGE_SPEC.md` | Formal runtime and access model spec |
| `docs/COMPILER_ARCHITECTURE.md` | Frontend/IL/backend lowering pipeline |
| `docs/picoscript-language-editor.md` | Language syntax, editor contract, completions |
| `docs/picoscript-hardware.md` | Hardware bytecode contract |

## Toolchain: two frontends, one IL, three backends

Both surface languages lower to a shared intermediate language (**PicoIL**) and
from there to any execution target — the same ISA and queue ABI everywhere
(LANGUAGE_SPEC.md §10). The bytecode backend runs on three bit-compatible VMs
(Python, C, JavaScript). Both frontends are **case-insensitive** for keywords and
variable names:

```
  C-syntax (.pc) ──┐                                ┌─→ bytecode → PicoVM (Python ref)
                   │                                ├─→ bytecode → picovm.c (bare metal)
                   ├─→ AST ─→ PicoIL ─→ lower ──────┼─→ bytecode → picovm.js (browser/Node)
  BASIC (.pbas) ───┘         (opt + regalloc)       ├─→ C  (toC)  → host cc → Thumb/AArch64
                                                    └─→ JS (toJS) → browser / Node
```

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

# Inspect each stage
python picoscript_build.py emit  examples/sum.pc --as il
python picoscript_build.py emit  examples/sum.pc --as bytecode --hex
python picoscript_build.py emit  examples/fizzbuzz.pbas --as c  -o out.c
python picoscript_build.py emit  examples/fizzbuzz.pbas --as js -o out.js

# Native build (Thumb / AArch64 via zig cc)
python picoscript_build.py native examples/sum.pc --target aarch64-freestanding-none -o sum.o
```

### Compile &amp; debug in the browser

`docs/playground.html` is a self-contained page (no server) showing **every
construct in both styles side by side**. The whole compiler is ported to JS
(`vm/picoc.js`), so you can **type C-syntax or BASIC source and compile it live**
in the browser, then run and single-step it on the inlined JS VM with full
register/output/PC inspection. You can also load a prebuilt example or paste
bytecode hex from `emit --as bytecode --hex`. Rebuild with `python gen_playground.py`.

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

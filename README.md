# PicoScript

Deterministic userland scripting language for embedded queue-driven processing in the Pico stack.

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
| `picoscript_runtime.py` | Reference runtime structures (arena, lease manager, profiling, queue batching) |
| `picoscript.py` | ISA helpers and instruction encoding |
| `picoscript_opcodes.py` | Opcode reference |
| `LANGUAGE_SPEC.md` | Formal runtime and access model spec |
| `docs/picoscript-language-editor.md` | Language syntax, editor contract, completions |
| `docs/picoscript-hardware.md` | Hardware bytecode contract |

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

FOREACH item AS i IN items
    DateTime.GetNow(R7)
    Locale.Format(R8, R7, "en_US")
ENDFOREACH
```

**Features:**
- Case-insensitive (keywords, identifiers, namespaces, methods)
- Whitespace-ignorant (preserves CRLF for line tracking)
- No semicolons or curly brackets
- Explicit block delimiters: `IF/THEN/ELSE/ENDIF`, `WHILE/ENDWHILE`, `FOREACH/IN/ENDFOREACH`, `SWITCH/CASE/ENDSWITCH`
- New library namespaces: `String.*`, `Number.*`, `Maths.*`, `DateTime.*`, `Locale.*`
- Same underlying bytecode ISA (v1 stable)

## Formal specification

- `LANGUAGE_SPEC.md` — runtime/access model/conformance levels, including queue handling, IRQ/SW_IRQ wake/sleep, lease-based access, performance hooks
- `picoscript_runtime.py` — reference host-side runtime structures for arena allocation, lease management, profiling, queue batching
- `docs/picoscript-language-editor.md` — language surface, editor contract, completions, diagnostics

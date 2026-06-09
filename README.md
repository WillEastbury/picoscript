# PicoScript

PicoScript is a fixed-width bytecode ISA plus a small source-language/compiler stack for executable cards.

This repository is the software-facing home for the language, compiler, opcode reference, and editor/spec work. Hardware implementations consume the bytecode contract but are intentionally kept separate so the language and editor can be tuned quickly.

## What is included

| Area | Files |
|------|-------|
| Compiler and decompilers | `picoscript_lang.py` |
| ISA helpers and examples | `picoscript.py` |
| Opcode reference | `picoscript_opcodes.py` |
| Hardware bytecode contract | `docs/picoscript-hardware.md` |
| Language/editor contract | `docs/picoscript-language-editor.md` |
| Example source | `examples/` |

## Quick start

No runtime dependencies are required beyond Python 3.11+.

```bash
python3 picoscript_lang.py
python3 picoscript.py
python3 picoscript_opcodes.py
```

## Specification boundary

PicoScript is split into two contracts:

- `docs/picoscript-hardware.md` defines the stable bytecode, register, opcode, and execution contract visible to hardware.
- `docs/picoscript-language-editor.md` defines source syntax, alternate views, diagnostics, completions, formatting, and editor behaviour.

The editor can evolve without changing emitted bytecode. Cards store bytecode, not source text.

## Current source view

The current primary input syntax is C#-style namespace calls:

```csharp
Net.Status(200);
Net.Type("text/html");
Net.Body();
Storage.Pipe(0, 1, 0, R0);
Flow.Return();
```

The compiler/decompiler stack can display the same bytecode as C# style, BASIC style, Python style, or raw hex.

## Status

This is an early standalone extraction of the PicoScript software stack. The immediate focus is language design, editor ergonomics, diagnostics, and bytecode round-tripping.

## License

MIT

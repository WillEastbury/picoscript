# picoscript

Embedded jump-table based programming language for the Pico suite.

## Current state

- Local repository created under `C:\source\picoscript`
- GitHub mirror: https://github.com/WillEastbury/picoscript
- Status: private scaffold for the embedded scripting layer

## Scope

- Run inside picoweb as a minimal embedded policy/runtime language
- Persist scripts and state through picowal
- Keep execution deterministic, bounded, and easy to audit

## Formal specification

- `LANGUAGE_SPEC.md` — draft formal language/runtime specification including queue handling, IPC/FIFO semantics, and IRQ/SW_IRQ wake/sleep model.
- `picoscript_runtime.py` — reference host-side arena/span/descriptor/storage API structures for fillable runtime primitives.

# The exception engine — real try/except/raise

This documents the real, working exception engine built on top of the
`Error.*` host namespace, replacing the "best-effort no-op" behavior found
and documented in `docs/DIALECT_PARITY.md` during the dialect-parity audit.

## Scope: which execution paths actually support this

**Fully working:** all **three** interpretive bytecode VMs -- **Python**
(`picoscript_vm.py`), **JavaScript** (`vm/picovm.js`), and **C**
(`vm/picovm.c`, fixed after this doc originally shipped only claiming the
first two — see "The C VM interpreter" below) -- for **every** frontend:
BASIC, Python-style, English, COBOL, Report, Functional (all six sharing
`picoscript_basic.py`'s `Lowerer`), and C-style (`picoscript_cfront.py`, an
independent AST + `Lowerer`, but reusing the same
`picoscript_il.ILBuilder`/`label_addr` and `Error.*` host ops, so the
underlying mechanism is identical). The JS compiler (`vm/picoc.js`) mirrors
all of this too -- `BLowerer.lowerTry`/`lowerOnBlock` (shared by the six
BASIC-family JS parsers) and each frontend's own JS parser grammar were
ported alongside the Python side, verified byte-identical
(`tests/test_js_port_exception_eventing.py`,
`tests/test_js_grammar_all_frontends.py`). This covers the CLI's `run`
command and the browser Playground's compile-and-run/step debugger for
every dialect.

### The C VM interpreter (`vm/picovm.c`) — fixed

Direct inspection during the namespace-equalization pass found that
`picovm.c` had **no `Error.*` host-hook dispatch at all** (`PV_HOOK_ERROR_*`
is registered in `HOST_HOOK_CODES` but was never referenced there) — a real
gap, and a real inaccuracy in an earlier revision of this doc/
`docs/FEATURE_MATRIX.md` that had (wrongly, unverified) claimed C VM parity.

The investigation turned up a fact that changed the whole scope of the fix:
**`laddr` needs no new C opcode at all.** It's a purely compile-time IL/
bytecode-assembly construct (`picoscript_il.py`'s
`_emit_const(..., force_wide=True)`) that lowers to plain `SUB`/`ADD`/`MUL`
words — the same "wide constant load" form used for any large integer
literal — which the C interpreter already executes correctly, opcode-for-
opcode identical to any other constant load. There is no `laddr` *runtime*
instruction to add; by the time bytecode reaches any interpreter, `laddr`
has already been resolved into ordinary arithmetic.

The two real gaps were:
1. The `Error.*` host-hook dispatch itself (straightforward — the same kind
   of addition as `Descriptor.*`/`Lease.*`/`Fifo.*`/`Log.*` elsewhere in
   this pass).
2. A way for a host hook (`Error.Raise`/`Error.Resume`) or a caught genuine
   VM fault (`pv_set_fault`) to redirect execution. Python/JS can mutate
   `vm.pc`/`this.pc` directly from a host hook because it's an object
   attribute; `pv_vm_run`'s `pc` is a **local C variable**, so a new
   `ctx->pending_jump`/`pending_jump_set` field was added as the channel
   back into the main loop, consumed once per instruction.

`pv_set_fault` (called by every genuine VM fault: bad jump, bad opcode, step
budget, call overflow) now checks the handler stack (`ctx->err_stack`) first
and redirects there instead of halting — exactly mirroring Python's
`except PicoFault` handling wrapped around `_step()` in `PicoVM.run()`.

Verified byte-identical to Python for: try/catch/finally/raise, nested
try/catch, uncaught raise (propagates as a real fault with the same code),
and — the architecturally riskiest case, since `pv_set_fault`/the main loop
change affects every program, not just ones using exceptions — a **genuine
VM fault** (bad computed jump) caught by an active handler, not just a
script-level `Raise`. See `tests/test_c_vm_error_parity.py`.

**Not supported, and explicitly, loudly rejected rather than silently
mis-compiled:**
- **Native C transpile** (`lower_to_c` → `vm/picovm.c` host ABI). Emitted C
  uses plain `goto` labels, not PC-addressable bytecode -- there is no
  runtime "program counter" value to load a label's address into, so
  `Error.SetHandler(pc)`'s whole model doesn't map onto this backend without
  a fundamentally different mechanism (e.g. `setjmp`/`longjmp`). Compiling a
  program containing `TryExcept`/`Raise` (any frontend, including C-style)
  with `--as c` (or `native`) raises a clear `ValueError` at compile time.
  Note this is a genuinely different problem from the interpreter gap above:
  emitted native C has no bytecode PC to jump to at all (it's straight-line
  `goto`-based C), so the "no new opcode needed" trick that fixed the
  interpreter doesn't apply here.
- **Native JS transpile** (`lower_to_js`, the "compile straight to a JS
  function" backend used for embedding compiled output, distinct from the
  *interpretive* `vm/picovm.js` above). Its block-switch dispatch model
  *could* represent a label address (a block index), but there's no
  try/catch wrapped around the dispatcher to actually catch a JS-level fault
  and redirect it -- that's a separate, not-yet-built piece. Also raises a
  clear `ValueError`.

If you need exception handling in native/bare-metal *transpiled* deployment
(as opposed to running bytecode on the C interpreter, which now works), this
is the gap to close next; it needs its own design (most likely a from-scratch
`setjmp`/`longjmp`-based C mechanism, and a try/catch-wrapped JS dispatcher),
not an extension of the bytecode-VM approach here.

## Update: C-style's JS mirror (`CParser`/`CLowerer`) — fixed

The gap above used to also list "C-style's JS mirror (`CParser`/`CLowerer` in
`vm/picoc.js`) does not have `try`/`catch`/`finally`/`raise`/`on` support" as
a third, deliberately-deferred implementation. This is now closed: `C_KW`
gained `try`/`catch`/`finally`/`raise`/`on`, `CParser` gained
`parseTry`/`parseOnBlock` (mirroring `picoscript_cfront.py`'s `parse_try`/
`parse_on_block` grammar exactly), and `CLowerer` gained `lowerTry`/
`lowerOnBlock` (mirroring `picoscript_cfront.py`'s `lower_try`/
`lower_on_block` — same handler-stack mechanism, same `labelAddr`/`laddr`,
same `Error.*`/`Event.*` host ops, just re-expressed against `CLowerer`'s own
`this.loop`/`this.varOf`/`this.b` conventions instead of `BLowerer`'s). This
is now a **third**, independently-verified implementation of the same
mechanism (BASIC-family's `BLowerer` port was the second), not sharing code
with either — deliberately re-derived rather than refactored into a shared
helper, to avoid entangling two already-independent lowerer families under
time pressure. Verified byte-identical bytecode to the Python `cfront`
compiler and byte-identical runtime output on the JS VM vs the Python VM, for
try/catch/finally/raise, nested try/catch, and `on Ns.Method{}` event
dispatch (both matching and non-matching events). See
`tests/test_cstyle_js_exception_eventing.py`.

## The mechanism

### Handler stack, not a single slot

`Error.SetHandler(pc)` **pushes** `pc` onto a handler stack
(`HostApi._error_handler_stack` in Python, `_errState.handlerStack` in JS) --
it used to overwrite a single slot, but nothing ever called it correctly
before this pass (see `docs/DIALECT_PARITY.md`'s bug writeup), so there was
no working behavior to preserve. A stack is what makes **nested try/except**
correct: the inner try's handler is active only for its own body; once its
except/finally has run (or the try body completed normally), a new
`Error.PopHandler()` call restores whatever handler (if any) was active
before it -- so a fault raised *inside* an except/finally body, or after the
try block entirely, is never mistakenly caught by that same try again.

`Error.SetHandler(0)` is a deliberate no-op push (preserves the pre-existing
"0 = no handler" convention many tests already encode) -- `HasHandler()` and
the fault-dispatch logic both check the *top of stack's truthiness*, not
just whether the stack is non-empty.

### `Error.Raise(code)` -- the new host op

A script-level "throw a value". If a handler is active (there's a non-zero
entry on top of the stack -- i.e. we're lexically inside a `TRY`), it jumps
straight to that handler's PC, exactly like a genuine VM fault would via
`PicoVM.run()`'s `PicoFault` handling -- just triggered in-band (inside a
host call) instead of via a caught Python/JS exception. `Error.Code()` in the
except body reads back exactly the raised value.

If **no** handler is active, `Error.Raise` does **not** silently swallow the
value -- it raises a real, uncaught `PicoFault(code, ...)` that propagates
out of `run()` exactly like an unhandled exception should (crashes the
program, or is caught by whatever *outer* frame -- a calling script, a test
harness -- happens to be watching for `PicoFault`).

Note: real VM faults (bad opcode/jump, div-by-zero, step budget, ...) and
script-raised codes share **one** `Error.Code()` channel -- a script
`Raise(2)` and a genuine bad-opcode fault are both readable as `Code() == 2`.
This is a documented, accepted tradeoff (most languages share one
errno/exception-code space between system and user-level errors), not a bug.

### `laddr` -- loading a label's address as a value

Every other IL construct that references a label (`jmp`, `call`, `cmpbr`,
`jmptab`) bakes it into a jump-target *immediate field*, resolved once at
bytecode-assembly time. `Error.SetHandler` needs the label's PC as an
ordinary *value* in a register instead (to pass as a host-call argument), so
`ILBuilder.label_addr(dst, label)` emits a new `laddr` IL instruction for
exactly that.

The bytecode assembler (`lower_to_bytecode_safe` in `picoscript_il.py`)
always expands `laddr` using the 8-word big-endian constant-load form
(`_emit_const(..., force_wide=True)`), **never** the 2-word small-immediate
form -- even when the label's PC would fit in 16 bits. This sidesteps a real
circular dependency: the two-pass assembler must know every instruction's
*width* before it can compute label PCs (pass 1), but the 2-word-vs-8-word
choice for an ordinary constant depends on the constant's *value* -- which
here is a label's PC, not knowable until pass 1 finishes. Since the 8-word
form's word *count* doesn't depend on the value (only the small form does),
committing to it unconditionally breaks the cycle. The cost is a few extra
words only for programs that actually use `try`/`except`.

### `lower_try` / `Raise` (`picoscript_basic.py`, shared by BASIC and
Python-style)

```
    laddr   addr, except_label
    host    Error.SetHandler(addr) -> ok      ; push
    <try body>                                 ; a fault/raise here jumps
                                                ; straight to except_label
    host    Error.PopHandler() -> ok           ; normal completion: pop
    <finally body>
    jmp     endtry
except_label:
    host    Error.PopHandler() -> ok           ; pop BEFORE running except/
                                                ; finally, so a fault in them
                                                ; propagates outward instead
                                                ; of looping back here
    <except body>
    host    Error.Clear() -> ok
    <finally body>
endtry:
```

`Raise(value)` lowers to `Error.Raise(eval(value))` (or `Error.Raise(0)` for
a bare `RAISE` with no value -- there is no "re-raise the current exception"
support; every `Raise` needs its own code).

## Testing

`tests/test_exception_engine.py` covers: happy path (except never runs,
finally always does), a caught `Raise` (and confirms the statement
immediately after `RAISE` inside the try body does *not* execute), an
uncaught `Raise` propagating as a real `PicoFault`, nested try/except (inner
catches without triggering the outer), a **genuine** VM-level fault (bad
computed jump, not a script `Raise`) caught the same way, byte-identical
bytecode between BASIC and Python-style sources for the same try/except/
finally/raise program, and JS-bytecode-VM/Python-VM parity for the exact
same compiled bytecode.

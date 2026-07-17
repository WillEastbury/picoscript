# The exception engine — real try/except/raise

This documents the real, working exception engine built on top of the
`Error.*` host namespace, replacing the "best-effort no-op" behavior found
and documented in `docs/DIALECT_PARITY.md` during the dialect-parity audit.

## Scope: which execution paths actually support this

**Fully working on all 5 execution paths**: the three interpretive bytecode
VMs -- **Python** (`picoscript_vm.py`), **JavaScript** (`vm/picovm.js`), and
**C** (`vm/picovm.c`, fixed after this doc originally shipped only claiming
the first two — see "The C VM interpreter" below) -- **and** both native
transpile backends -- **native C** (`lower_to_c`) and **native JS**
(`lower_to_js`), fixed by the structured-`trycatch`-IL redesign described
below, after this doc originally shipped claiming they were a fundamentally
harder, not-yet-attempted gap -- for **every** frontend: BASIC, Python-style,
English, COBOL, Report, Functional (all six sharing `picoscript_basic.py`'s
`Lowerer`), and C-style (`picoscript_cfront.py`, an independent AST +
`Lowerer`, but reusing the same `picoscript_il.ILBuilder.trycatch` and
`Error.*` host ops, so the underlying mechanism is identical). The JS
compiler (`vm/picoc.js`) mirrors all of this too -- `BLowerer.lowerTry`/
`lowerOnBlock` (shared by the six BASIC-family JS parsers) and each
frontend's own JS parser grammar were ported alongside the Python side,
verified byte-identical (`tests/test_js_port_exception_eventing.py`,
`tests/test_js_grammar_all_frontends.py`), and `CParser`/`CLowerer` (C-style's
JS mirror) gained the same support too (see "C-style's JS mirror" below).
This covers the CLI's `run` command and the browser Playground's
compile-and-run/step debugger for every dialect, plus both native
C/JS transpile targets.

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

## Update: native C/JS transpile now support this too (structured `trycatch` IL)

The scope note below (kept for historical context) described native C/JS
transpile as needing "a fundamentally different mechanism... not an
extension of the bytecode-VM approach". That diagnosis was right, and the
fix follows exactly that path: `TryExcept` is no longer flattened into
`laddr`/`Error.SetHandler`/`label`/`jmp` at `Lowerer` time at all. Instead,
`ILBuilder.trycatch` builds a **structured** IL node carrying nested
`try_body`/`except_body`/`finally_body` instruction lists (captured via
`begin_capture`/`end_capture`), and each consumer decides how to realize it:

- **`lower_to_bytecode_safe`** (and the plain `lower_to_bytecode`) expand it
  via `_flatten_trycatch` into exactly the classic flat
  `laddr`/`Error.SetHandler`/`label`/`jmp` form this doc originally
  described — byte-identical to before this change, verified by the full
  existing exception-engine test suite.
- **`lower_to_c`** compiles the structured node directly into **plain
  `goto`/labels** — no `setjmp`/`longjmp` needed after all, once the
  structure survives long enough to use it: the handler's C label is known
  at *compile time* (it's a label in the SAME IL the Lowerer already built),
  so `Error.SetHandler`'s "load a label's address as a runtime value"
  problem (the actual reason `laddr` existed) simply doesn't arise for
  native C. A `Raise` lexically inside its own try's handler range emits
  `goto handler;` directly. A `Raise` with **no** in-function handler (either
  truly uncaught, or the handler is in a *caller* — this function was
  invoked from inside a try) sets `ctx->raise_active` and returns instead;
  every emitted subroutine call site checks `ctx->raise_active` right after
  the call and either `goto`s its own in-function handler (if any) or keeps
  propagating by returning too — unwinding the native C call stack one
  frame at a time, entirely standard, portable C (no `volatile`/UB concerns
  `setjmp`/`longjmp` would have brought). See `tests/test_native_toc_trycatch.py`.
- **`lower_to_js`** compiles the structured node into a **real JS
  `try { } catch (e) { } finally { }`**, and `Raise` into a real
  `throw new PicoRaise(code)`. Unlike native C, this needs no return-code
  propagation at all — a real JS `throw` naturally unwinds across function
  calls to the nearest enclosing `try`, exactly matching the semantics of a
  handler stack without building one. The one JS-specific complication:
  each of `try_body`/`except_body`/`finally_body` gets its **own**
  independent, recursively-built basic-block state machine (JS has no
  `goto`, so nested control flow like a `while` loop inside a try body needs
  its own local dispatcher); a jump that crosses a try boundary into an
  *enclosing* scope (e.g. a `break` inside a try body targeting a loop that
  wraps it) resolves against that outer scope and escapes via a labeled
  `continue` rather than an unlabeled one. See `tests/test_native_js_trycatch.py`
  (includes both a loop entirely inside a try, and a `break` crossing the
  try boundary into an enclosing loop).

Both are verified against the same Python-VM reference outputs as the
bytecode paths, for: try/catch/finally/raise, happy path (no exception),
nested try/catch, uncaught raise, and a cross-function raise (a subroutine
call inside a try, where the subroutine itself raises) — see "Cross-function
raise: a call-stack-unwinding bug, found and fixed" below for the story of
how this last case surfaced a real, pre-existing bug in all three bytecode
VMs, since fixed.

### Cross-function raise: a call-stack-unwinding bug, found and fixed

Testing native C's cross-function-raise support against the Python VM as a
reference (as this redesign's verification methodology requires) surfaced a
real, pre-existing bug in the interpretive bytecode VMs (Python, JS, and the
C interpreter — all three shared the identical handler-stack/PC-redirect
mechanism, so all three had it): when `Error.Raise` (or a caught genuine
fault) jumps straight to a handler's PC from **inside a called subroutine**,
`vm.call_stack` was never unwound (only `vm.pc` was redirected) — so the
stale return address pushed by the `CALL` into that subroutine (pointing
just past the call, i.e. into the middle of the try body that should have
been skipped) was still sitting on the call stack. Whatever `RETURN`
eventually executed next (e.g. the implicit one ending the top-level
program) popped that stale address and resumed execution there instead of
wherever it actually should — silently re-running code that was supposed to
be skipped.

Concretely: `try { x = 1; boom(); x = 999; } catch { x = x + 100; }` where
`boom()` does `raise 55;` — the **correct** result is `x == 101` (`boom()`
raises, `x = 999;` is skipped, catch runs). The **pre-fix bytecode VMs**
produced `x == 999` after first emitting a spurious extra `print` of `101`
(both values got printed — the handler ran once correctly, then the stale
call-stack entry resumed the try body from where `boom()` was called,
completing it a second time). Native C's return-code-propagation design never
had this bug (it unwinds via real C function returns, which correctly
discard each frame's state); native JS is unaffected for a different reason —
a real JS `throw` unwinds the actual JS call stack, so there's no analogous
"stale return address" concept to leak in the first place.

**The fix** (applied to all three bytecode VMs): `Error.SetHandler` now
records the call-stack depth at push time in a parallel array —
`picoscript_vm.py`'s `_error_handler_call_depth`, `vm/picovm.js`'s
`_errState.callDepth`, `vm/picovm.c`'s `ctx->err_call_depth[]` (parallel to
`_error_handler_stack`/`_errState.handlerStack`/`ctx->err_stack[]`
respectively). `Error.Raise` and the genuine-fault-catch path (both the
in-band host-hook path and `PicoVM.run()`'s/`picovm.js`'s `run()`'s/
`pv_set_fault`'s main-loop fault handler) now truncate the call stack back to
that recorded depth immediately before redirecting `pc` to the handler —
discarding any return addresses pushed by subroutines called after the
handler was armed. `Error.PopHandler` pops the parallel depth entry too, to
stay in sync. Verified: the cross-function-raise example above now produces
`x == 101` identically across Python VM, JS VM (`vm/picovm.js`), and the C VM
interpreter (`vm/picovm.c`) — matching native C/JS. See
`tests/test_c_vm_error_parity.py::check_cross_function_raise_unwinds_call_stack`,
`tests/test_exception_engine.py::test_js_bytecode_vm_cross_function_raise_unwinds_call_stack`,
and `tests/test_native_toc_trycatch.py`'s cross-function test (now asserting
equality across all paths, no longer documenting a discrepancy).

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

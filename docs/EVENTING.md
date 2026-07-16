# Eventing — real `ON` block dispatch

This documents the real, working `ON Ns.Method: ... END ON` event-dispatch
mechanism, replacing dead code found during the investigation that preceded
`docs/EXCEPTION_ENGINE.md`.

## What was broken

`lower_on_block` (`picoscript_basic.py`'s `Lowerer`) emitted the `ON` body as
a labelled "subroutine", jumped *over* it during normal execution, then
called `host(event_ns, "Register", (), None)` -- e.g. for `ON Ui.Click:`
this literally called `Ui.Register()`. There has never been a runtime
`Register` host-hook handler anywhere (Python VM, JS VM, or C VM), and
nothing else in the compiled program ever jumped to that label. So **every
`ON` block silently compiled to permanently unreachable code** -- it parsed
fine, compiled fine, ran without error, and simply never fired, no matter
what events were posted.

This was distinct from -- and did not affect -- `Event.*` itself (`Post`,
`Next`, `Type`, `Target`, `Data`, `Count`, ...), which was already a real,
working FIFO queue in the VM host state, and from Workflow's `RAISE`/`ON`
steps (`picoscript_workflow.py`), which already worked by lowering to a
hand-generated poll loop over that same real queue (see `docs/EVENTS.md`).

## The fix: reuse the proven poll-loop pattern, add a compile-time type code

`OnBlock` now lowers to an **inline drain-and-dispatch loop**, directly
modeled on Workflow's already-tested `ON`/`SUBSCRIBE` step lowering:

```
    <Event.Count() into cnt>
    i = 0
on:
    if i >= cnt: goto endon
    <Event.Next() into __event__>
    if Event.Type(__event__) != type_code: goto onskip
        <body>
onskip:
    i = i + 1
    goto on
endon:
```

The one piece Workflow's steps don't need (because the visual designer's
`event` field is just an opaque integer the author picks) but `ON Ns.Method`
does: turning `Ns.Method` into a stable integer `type_code`.
`picoscript_basic.event_type_hash(ns, method)` computes this **at compile
time** using the exact FNV-1a algorithm `picoscript_vm.py`'s `Map.Hash`
already implements at runtime (same offset basis `0x811C9DC5`, same prime
`0x01000193`) -- an established, precedented hash primitive in this
codebase, not a new one invented for this feature. Because it's computed
once, at compile time, and baked into the bytecode as a plain integer
constant, there is **no runtime string hashing anywhere** and therefore
**zero cross-VM parity risk** -- every execution path just sees the same
integer literal, exactly like any other `CONST`.

The event id is bound to the reserved variable `__event__` for the body to
read via `Event.Target(__event__)` / `Event.Data(__event__)`. `OnBlock`'s
grammar has no `AS var` clause (unlike Workflow's `ON` step, which does), so
a clearly-reserved internal name is used rather than risking a collision
with a same-named user variable.

## How to raise a matching event

There is currently no dedicated "raise a named `Ns.Method` event" statement
in any frontend -- posting an event that a given `ON` block will catch means
calling `Event.Post(event_type_hash(ns, method), target)` with a **matching**
hash. From BASIC source this is `EVENT POST <type> <target>` (BASIC's
`EVENT`/`UI` keywords have their own dedicated call-body syntax --
`Event.Post(...)` dotted-call syntax does *not* parse, since `EVENT` is a
reserved keyword routed through `_parse_uievt_body` instead of the generic
host-call path). Adding first-class script syntax for "raise a named event"
(e.g. `RAISE Ui.Click(target)` reusing the namespace/method the way `ON`
does) is a deliberate, separate follow-up, not attempted here -- this pass's
scope was closing the "`ON` blocks are dead code" bug, not adding new raise
syntax. In practice, the actual raiser is often host-injected (a real UI
click, an HTTP request) rather than another script statement, so the
compile-time-computed `event_type_hash` is exposed as a plain Python function
specifically so host integrations can compute a matching type code too.

## A known, inherited limitation: one drain owner at a time

Like Workflow's `ON` step (which this mirrors), the poll loop calls
`Event.Next()` -- which **dequeues** -- for every pending event checked
during this pass, whether or not its type matches. A non-matching event is
therefore consumed and discarded, not requeued. If a program has two `ON`
blocks (or an `ON` block and a Workflow `ON` step) for *different* event
types and events of both types are pending, whichever poll loop runs first
will drain and discard the other's events. This is not a new defect
introduced here -- it is an existing, already-shipped, already-tested
characteristic of the underlying pattern (see `docs/EVENTS.md`'s Workflow
`ON` example) that this change deliberately mirrors for consistency rather
than silently diverging from. Fixing it properly would mean the `Event.*`
queue supporting non-destructive peek/requeue-on-mismatch, or per-consumer
cursors -- a separate, larger redesign of the queue primitive itself.

## Testing

`tests/test_eventing.py` covers: `event_type_hash` determinism and case
insensitivity, an `ON` block dispatching only on a matching event (posted via
real BASIC `EVENT POST` source, reading the target back via
`EVENT TARGET __event__`), an `ON` block never firing when no matching event
was posted, and (via direct AST construction, to isolate the lowering from
BASIC's `EVENT`-keyword parsing quirks) multiple matching events in one
drain pass being dispatched correctly.

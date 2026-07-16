# Events — RAISE & subscribe

PicoScript has a first-class, deterministic event model built on the reactive
`Event.*` queue. It is a **uniform host-call primitive** (present in every
frontend) plus ergonomic workflow steps, and it runs bit-identically on the
Python, JS, and C# VMs.

## The `Event.*` queue (the primitive)

An in-runtime FIFO of `(type, target, data)` records. `Post` enqueues, `Next`
dequeues the oldest (0 = empty). External sources (browser UI, PIOS timers/IRQs)
inject through the same `Post` path, so a program's event loop is identical in
the sim and on hardware.

| Method | Hook | Signature | Effect |
|--------|------|-----------|--------|
| `Event.Post(type, target)` | `0x0180` | `rs1=type rs2=target → rd=eventId` | Enqueue an event; returns its id. |
| `Event.Next()` | `0x0181` | `→ rd=eventId` | Dequeue the oldest id (0 if empty). |
| `Event.Type(id)` | `0x0182` | `rs1=id → rd=type` | The event's type. |
| `Event.Target(id)` | `0x0183` | `rs1=id → rd=target` | The event's target. |
| `Event.Data(id)` | `0x0184` | `rs1=id → rd=span` | The event's data span (0 = none). |
| `Event.SetData(id, span)` | `0x0185` | `rs1=id rs2=span → rd=ok` | Attach a data span. |
| `Event.Count()` | `0x0186` | `→ rd=count` | Pending queue depth. |

These hook codes are identical across `vm/pico_hooks.*` (Python/JS) and the C#
`WorkflowHost` (`developercli/workflow/WorkflowHost.cs`). Bind a `WorkflowHost`
on the C# `PicoVm` so `Event.*` resolves; the Python and JS reference VMs
implement the queue built-in.

Because host calls exist in **every** frontend, `Event.Post(...)` / `Event.Next()`
are directly usable from C, BASIC, Python, English, COBOL, report and functional
source — no language-specific syntax required.

## Workflow `RAISE` / `ON` steps

The visual-workflow dialect adds ergonomic steps that lower to the primitive
above (see [WORKFLOW_DIALECT.md](WORKFLOW_DIALECT.md)). Available in all three
workflow compilers — `picoscript_workflow.py`, `BareMetal.WorkflowPico` (JS),
and the playground designer.

### RAISE / EMIT — post an event

```json
{ "type": "RAISE", "event": 7, "target": 3, "result": "eid" }
```
→ `Set eid to Event.Post(7, 3).`

### ON / SUBSCRIBE — handle events (block, closed by `END`)

```json
{ "type": "ON", "event": 7, "var": "event" },
  { "type": "SET", "name": "hits", "expr": "hits + 1" },
{ "type": "END" }
```
lowers to a bounded drain-and-dispatch loop:
```
For each _on0 from 0 to (Event.Count() minus 1):
    Set _ev1 to Event.Next().
    If Event.Type(_ev1) is 7:
        Set event to _ev1.
        Set hits to hits plus 1.
```
The handler body runs once for each pending event whose type matches; `var`
(default `event`) is bound to the event id so the body can read `Event.Target` /
`Event.Data`.

## `RAISE` opcode / swirq — NOT the same as the BASIC/Python-style `RAISE` statement

**Correction:** an earlier version of this doc described the BASIC/Python-style
`RAISE` *statement* as firing this opcode. That was only ever true for a brief,
explicitly-interim safety fix (see `docs/DIALECT_PARITY.md`'s bug writeup) --
`RAISE`/`TryExcept` now has a real exception engine
(`docs/EXCEPTION_ENGINE.md`): `RAISE <value>` lowers to `Error.Raise(value)`,
which jumps to the nearest `Error.SetHandler`-registered handler (an
enclosing `TRY`/`EXCEPT`) or propagates as a real, uncaught VM fault if none
is active. It has nothing to do with the opcode below.

Separately, the `RAISE` **opcode** (`0xE`) is a low-level software interrupt:
fire-and-forget on a channel, surfaced by the host (the reference VM logs
`raise swirq channel=N`; all three VMs -- Python, JS, C -- currently just log
it, no script-level statement or host call exposes it yet, so it's presently
inert). Use the `Event.*` queue for application pub/sub; use `Error.Raise`
(via `TRY`/`EXCEPT`/`RAISE`) for script-level exceptions; the swirq opcode is
reserved for a future kernel-coupled interrupt primitive, not implemented as
a language-level feature today.

## Browser bridge — `BareMetal.PubSub`

In the browser, the `BareMetal.Workflow` engine bridges the workflow event steps
to the app event bus [`BareMetal.PubSub`](https://github.com/WillEastbury/BareMetalJsTools):

- `RAISE` → `PubSub.emit(event, data)`
- `ON` → `PubSub.on(event, handler)` (the handler body runs when the event fires)

So a browser workflow can raise onto and subscribe from the same bus the rest of
the app uses, while the compiled bytecode uses the `Event.*` queue on the VM.

## Reserved keys / channels

| Key | Meaning |
|-----|---------|
| `4000` (`0x0FA0`) | reject flag (non-zero ⇒ reject the operation) |
| `4001` (`0x0FA1`) | reject message code |

(Scratch keys, shared with `WorkflowHost.cs` and `flow.js`.)

## Verification

- Reference: `tests/test_workflow_frontend.py` (RAISE posts events; ON drains and
  dispatches → 2 hits).
- Browser: `baremetaljstools` `BareMetalWorkflowPico` + `BareMetalWorkflowEvents`
  (RAISE/ON on the VM and the PubSub bridge).
- Cross-language: `developercli/workflow/test/oracle.js` `wf_events` case — the C#
  `PicoVm` reproduces the JS reference output (`hits = 2`) bit-identically.

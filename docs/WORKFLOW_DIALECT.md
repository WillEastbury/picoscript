# Workflow dialect — visual workflows → PicoScript

The **workflow** frontend (`picoscript_workflow.py`, `--lang workflow`) compiles a
visual-workflow step list to PicoIL. It is the reference, rebuild-from-scratch spec
for the cross-language workflow compiler that also ships as:

- **JavaScript** — `baremetaljstools/src/BareMetal.WorkflowPico.js` (the visual
  designer's "Compile to PicoScript" button), and the graph variant
  `developercli/tools/forge_assets/flow.js`.
- **C#** — the runtime target `developercli/workflow/PicoVm.cs` +
  `WorkflowHost.cs`, differential-tested by `developercli/workflow/test/oracle.js`.

All of them emit the **same natural-English PicoScript** and therefore the same
bytecode, so a workflow authored once runs bit-identically in the browser, on the
Python reference VM, on RP2350/PIOS, and on the C# VM.

**Not the same thing as the AST designer.** Workflow is a deliberately flat,
externally-constrained step list (`SET`/`IF`/`FOR`/`FOREACH`/`END` markers) shaped
by an existing flowchart-style UI contract shared with the repos above; anything
outside that vocabulary (ternaries, sub-definitions, ...) rides through as an
opaque `RAW` English string (see `astToWorkflow` in `vm/picoc.js`). For editing the
*full* grammar as a structural tree instead of a flowchart, see
`docs/ast_designer_spike.html` + `picoscript_ast.py` (`--lang ast`, `.ast`/`.astjson`
files) — the canonical AST-as-JSON that every dialect already lowers to.

## Input: the step list

A workflow is a flat JSON array of step objects with block markers (`END` closes
`IF`/`FOR`/`FOREACH`; `ELSE` splits an `IF`). This is exactly the shape the
`BareMetal.Workflow` designer produces.

```json
[
  { "type": "SET", "name": "data", "value": [10, 20, 30, 40] },
  { "type": "SET", "name": "sum", "value": 0 },
  { "type": "FOREACH", "var": "item", "in": "data" },
  { "type": "SET", "name": "sum", "expr": "sum + item" },
  { "type": "END" },
  { "type": "IF", "condition": "sum >= 50" },
  { "type": "LOG", "message": "sum" },
  { "type": "END" }
]
```

```python
from picoscript_workflow import compile_workflow      # -> PicoIL
from picoscript_build import to_bytecode               # or --lang workflow
il = compile_workflow(open("flow.wf").read())
```

`compile_workflow(source)` accepts a JSON string, a Python list of step dicts, or
an object with a `steps` array, and returns PicoIL like every other frontend.
`workflow_to_english(steps)` returns `(english_source, warnings)` for inspection.

## Step types

| Step | Fields | Lowering |
|------|--------|----------|
| `SET` | `name`, `value` \| `expr` | `Set <name> to <rhs>.` — `expr` is word-operator translated; `${expr}` values become expressions. |
| `SET` (array literal) | `name`, `value: [..]` | Materialises into `Memory` (see [Arrays](#arrays)). |
| `IF`/`ELSE`/`END` | `condition` | `If <cond>:` / `Otherwise:` + indentation. Empty blocks get a `Set _nop to 0.` filler. |
| `FOR` | `var`, `from`, `to`, `step?` | `For each <var> from <from> to <to> [by <step>]:` (inclusive). |
| `FOREACH`/`FOREACHP` | `var`, `in` | Value iteration over an array (see [Arrays](#arrays)); `FOREACHP` lowers to sequential (warns). |
| `LOG` | `message` | `Print <value>.` for numeric/identifier/`${expr}`; free text → comment + warning. |
| `WAIT` | `ms` | `Timer.After(<ms>).` (non-blocking; warns). |
| `RAISE`/`EMIT` | `event`, `target?`, `result?` | `Event.Post(<event>, <target>).` (posts onto the reactive event queue; `result` captures the event id). |
| `ON`/`SUBSCRIBE` … `END` | `event`, `var?` | Block: drains the `Event.*` queue and runs the handler body for each pending event of `<event>` (binds `var`, default `event`, to the event id). |
| `LOAD` | `name`, `from`, `key?` | `variable` → assignment; `memory`/`scratch` → `Memory.Get`/`Context.GetScratchValue`; storage/HTTP → comment + warning. |
| `SAVE` | `name`, `to`, `key?` | `variable` → assignment; `memory`/`scratch` → `Memory.Set`/`Context.SetScratchValue`; storage → comment + warning. |
| `WEB` | `method`, `url`, `result?` | `# WEB …` comment + warning (needs a host transport hook). |
| `CALL` | `workflow` | `# CALL …` comment + warning (nested workflows compile separately). |

Unknown identifiers are sanitised to `[A-Za-z0-9_]` (leading digits prefixed `_`).

## Expressions and operators

`expr`/`condition` are a JS-ish subset, translated to **English word operators**:

| JS | English | JS | English |
|----|---------|----|---------|
| `+ - * /` | `plus minus times divided by` | `== ===` | `is` |
| `%` | `modulo` | `!= !==` | `is not` |
| `> <` | `is greater than` / `is less than` | `>= <=` | `is at least` / `is at most` |
| `&& \|\|` | `and` / `or` | unary `-` | `0 minus …` |

These spellings are identical across `picoscript_workflow.py`,
`BareMetal.WorkflowPico`, and `flow.js`.

## Arrays

The VM is a deterministic 32-bit integer machine, so an **array is a base address
+ length in `Memory`**: a `SET name [a, b, c]` materialises the elements into
consecutive `Memory` cells (base default `8192`, override with `array_base`), and
records `name → (base, len)`. `FOREACH` then iterates element **values**:

```
Memory.Set(8192, 10).
Memory.Set(8193, 20).
Memory.Set(8194, 30).
Set data to 8192.
Set data_len to 3.
Set sum to 0.
For each _fe0 from 0 to 2:
    Set item to Memory.Get(8192 plus _fe0).
    Set sum to sum plus item.
Print sum.
```

`FOREACH` resolves its `in` from an array variable (declared by `SET`, aliased via
`LOAD from variable`) or an inline literal (`"in": [1,2,3]`). Runtime arrays that
can't be resolved at compile time lower to a single iteration with a warning.

> The natural-English surface language also has a `For each X in <collection>:`
> form, but that iterates a **byte span** via `Span.Len`/`Span.Get`. The workflow
> frontend deliberately uses the `Memory`-based **integer** array scheme above so
> it matches the JS/C# compilers and the differential oracle.

## Data ABI (hook codes)

Field/scratch and memory access use the exact hook codes implemented by both the
JS bundle's VM and the C# `WorkflowHost`:

| Purpose | English | Hook code |
|---------|---------|-----------|
| Read field/scratch | `Context.GetScratchValue(key)` | `0xeb` |
| Write field/scratch | `Context.SetScratchValue(key, value)` | `0xea` |
| Read memory/array | `Memory.Get(addr)` | `0x37` |
| Write memory/array | `Memory.Set(addr, value)` | `0x36` |

Reserved scratch keys (workflow control, matching `WorkflowHost.cs` and
`flow.js`): reject = `4000` (`0x0FA0`), message = `4001` (`0x0FA1`).

On the C# `PicoVm`, bind a `WorkflowHost` so `Memory`/`Context` resolve; the Python
and JS reference VMs implement them built-in.

## Representable vs. host-only

- **Faithful:** integer variables, arithmetic, comparisons, `and`/`or`, `IF`/`ELSE`,
  `FOR`, nested blocks, integer arrays + `FOREACH` over values, `LOAD`/`SAVE` to
  `variable`/`memory`/`scratch`, `LOG` of numbers/variables.
- **Best-effort (warns):** `FOREACHP` (sequential), unresolvable runtime arrays
  (single iteration), `WAIT` (non-blocking), string values.
- **Host-only (comment + warning):** `WEB`, storage/HTTP `LOAD`/`SAVE`, `CALL`,
  string interpolation, non-scalar values. Always check the `warnings` list — an
  empty list means every step lowered faithfully.

## Differential harness

`developercli/workflow/test/oracle.js` compiles representative workflows through
the JS bundle, runs the JS reference VM, and writes `wf_cases.json`
(`words`/`expRegs`/`expOutHex`). The C# `PicoVm` must reproduce them exactly (with
a `WorkflowHost` bound for `Memory`). The Python frontend here is validated the
same way in `tests/test_workflow_frontend.py`; the canonical cases
(`array_sum → 100`, `array_filter → 32`) agree across all three implementations.

# PicoScript Generation Prompt (for LLMs / coding agents)

This file is a **ready-to-use system prompt** for an agent or model that must
*generate* valid PicoScript. Paste the block below (everything between the
`────` rules) as the system / instruction prompt; append the user's task. The
notes after it explain the design and how to keep the prompt in sync with the
compiler.

Everything here is grounded in the actual compiler (`picoscript_*.py`,
`vm/picoc.js`) and runtime (`picoscript_vm.py`, `vm/picovm.js`). Only constructs
and host calls that compile **and run** on the reference VM are documented as
usable.

---

────────────────────────────────────────────────────────────────────────────
You write **PicoScript**: a tiny, deterministic, integer-only imperative
language that compiles to a frozen 16-opcode bytecode and runs identically on a
Python VM, a bare-metal C VM, and a JavaScript VM.

## Surface styles

PicoScript has four interchangeable surface styles. **A program is written in
exactly one style.** Pick the style the user asks for; default to **C-syntax**
if unspecified. All four lower to identical bytecode, so the choice is purely
about readability.

| Style    | Looks like                         | Block syntax                |
|----------|------------------------------------|-----------------------------|
| `c`      | C / curly braces                   | `{ … }`, `;` statements     |
| `basic`  | BASIC, uppercase keywords          | `IF…ENDIF`, line statements |
| `python` | Python, significant indentation    | `:` + indent                |
| `english`| Plain imperative English sentences | `:` + indent, `.` ends lines|

Keywords, variable names and `Namespace.Method` names are **case-insensitive**.

## Hard rules (violating these produces code that will not compile or run)

1. **Integers only.** Every value is a signed 32-bit int. There are no floats,
   no first-class strings, no objects, no arrays-as-values. (Text is handled as
   byte *spans*; see "Text & output".)
2. **One global scope.** All variables are global. Subroutines take **no
   parameters** and share the global variables (use them like shared registers).
3. **≤ 16 live variables** at once (they map to registers `R0`–`R15`; the
   allocator spills, but keep programs small).
4. **No string variables.** A string literal is only valid as a direct argument
   to `print(...)` or a host call that expects a span (e.g. `Net.Type`,
   `Json.Key`). You cannot assign a string to a variable.
5. **Deterministic.** No wall-clock, no ambient I/O. The only randomness is
   `Random.U32` (explicit). The only output is `print` / `Io.*` / `Net.*`.
6. **Host calls take at most 2 value arguments and return at most 1 value**
   (`rd = Ns.Method(a, b)`). Never pass 3+ args to a namespace method.

## Constructs (every construct exists in every style)

| Construct          | c                                   | basic                              | python                       | english                                  |
|--------------------|-------------------------------------|------------------------------------|------------------------------|------------------------------------------|
| declare / assign   | `int x = e;`                        | `DIM X = e` / `X = e`              | `x = e`                      | `Set x to e.`                            |
| print number       | `print(x);`                         | `PRINT X`                          | `print(x)`                   | `Print x.`                               |
| print string       | `print("hi");`                      | `PRINT "hi"`                       | `print("hi")`                | `Print "hi".`                            |
| if / else          | `if (c) {…} else {…}`               | `IF c THEN … ELSE … ENDIF`        | `if c:` / `elif` / `else:`   | `If c:` / `Otherwise if c:` / `Otherwise:`|
| while              | `while (c) {…}`                     | `WHILE c … ENDWHILE`              | `while c:`                   | `While c:` / `As long as c:`             |
| do (post-test)     | `do {…} while (c);`                 | `DO … LOOP UNTIL c`               | `do:` … `until c`            | `Repeat:` … `Until c.`                   |
| for (counted)      | `for (i=a;i<=b;i++) {…}`            | `FOR I=a TO b … NEXT`             | `for i in range(a,b+1):`     | `For each i from a to b:`                 |
| for (0..n-1)       | `for (i=0;i<n;i++) {…}`             | `FOREACH I IN n … ENDFOREACH`     | `for i in range(n):`         | `Repeat n times with i:`                  |
| switch             | `switch(x){case N: … break; default: …}` | `SWITCH x CASE N … DEFAULT … ENDSWITCH` | `match x:`/`case N:`/`case _:` | `Choose x:`/`When N:`/`Otherwise:`  |
| dispatch (jump table) | `dispatch(x){case N: … break; default: …}` | `DISPATCH x CASE N … DEFAULT … ENDDISPATCH` | `dispatch x:`/`case N:`/`case _:` | `Dispatch on x:`/`When N:`/`Otherwise:` |
| goto / label       | `L: … goto L;`                      | `L: … GOTO L`                      | `label L` … `goto L`         | `Label L.` … `Go to L.`                  |
| subroutine         | `void f(){…}` / `f();`              | `SUB f … ENDSUB` / `GOSUB f`       | `def f():` / `f()`           | `Define f:` / `Do f.`                    |
| break / continue   | `break;` / `continue;`              | `BREAK` / `SKIP`                   | `break` / `continue`         | `Stop.` / `Skip.`                        |
| return             | `return;`                           | `RETURN`                           | `return`                     | `Return.`                                |

## Expressions & operators

| Op            | c        | basic           | python      | english                       |
|---------------|----------|-----------------|-------------|-------------------------------|
| arithmetic    | `+ - * /`| `+ - * /`       | `+ - * /`   | `plus minus times divided by` |
| modulo        | `%`      | `MOD`           | `%`         | `modulo`                      |
| compare       | `== != < > <= >=` | `= <> < > <= >=` or `EQ NE LT GT LE GE` | `== != < > <= >=` | `is`, `is not`, `is greater than`, `is less than`, `is at least`, `is at most`, `exceeds` |
| logical       | `&& \|\| !` | `AND OR NOT`  | `and or not`| `and or not`                  |
| ternary       | `c ? a : b` | `IIF(c,a,b)` | `a if c else b` | `a if c otherwise b`      |
| increment     | `x++` / `x--` | `INC X`/`DEC X` | `x += 1`/`x -= 1` | `Increase x by 1.`/`Decrease x by 1.` |
| compound      | `+= -= *= /= %=` | `+= -= *= /=` | `+= -= *= /= %=` | `Increase/Decrease/Multiply/Divide x by …` |

> In `english`, `times` is also the `*` operator, so `Repeat n times:` needs `n`
> to be a simple value or a parenthesised expression — e.g. `Repeat (a+b) times:`.

## Text & output

Text exists only as **UTF-8 byte spans** in arena memory. To produce text:

- `print("literal")` — emit a string literal (works in every style). Internally
  it stages the bytes into scratch arena and calls `Io.Write`.
- `print(number)` — emit a value's 4 big-endian bytes.
- Build bytes by hand: `Memory.Set(addr, byteval)` then
  `span = Span.Make(startAddr, length)`, then `Io.Write(span)`.
- Streaming JSON / XML: open a writer with `Utf8Writer.New(...)`, then call
  `Json.*` / `Xml.*` (auto-commas and escaping are handled by the runtime), and
  finish with `Io.Write(Utf8Writer.ToSpan(w))`.

## Host namespaces that actually run on the VM

Only these are implemented end-to-end; do **not** invent others.

- `Net.Status(code)`, `Net.Type("mime")`, `Net.Body(span)` — HTTP response.
- `print(...)`, `Io.Write(span)`, `Io.WriteByte(reg)` — output buffer.
- `Memory.Set(addr,val)`, `Memory.Get(addr)` — byte memory.
- `Span.Make(addr,len)`, `Span.Slice(span,off)`, `Span.Materialize(span)`,
  `Span.Len(span)`, `Span.Get(span,idx)` — spans (Slice = zero-copy view,
  Materialize = copy).
- `Storage.UsePack(n)`, `Storage.AddCard()`, `Storage.EditCard(id)`,
  `Storage.SetField(span,val)`, `Storage.GetField(span)`,
  `Storage.DeleteCard(id)`, `Storage.QueryCard(querySpan)`,
  `Storage.QueryResult(i)` — program-level card store.
- Prefer active-record card syntax in C-style examples when a pack has a schema:
  `Order ord = Storage.GetCard(pack,id); ord.qty = 42; Storage.SaveCard(ord);`.
  Use `Storage.QueryCards(pack,"qty > 40")` + `Storage.QueryResult(i)` for
  materialized query loops. Use the low-level `SetField/GetField` API only for
  schema-less/ordinal cards.
- Large card / payload windows: `Storage.SetSlice/CardLen/ReadSlice/WriteSlice`,
  `Req.SetSlice/BodySlice/BodyLen`, `Stream.SetSlice/Slice`,
  `Event.SetSlice/DataSlice/DataLen`.
- Transformer/inference primitives: `Tensor.SetShape/DotI8/MatVecI8/AddI32/
  MulI32/ScaleI32/ReluI32/RmsNormI32/RoPEI32/SoftmaxI32/ArgMaxI32` and
  `BitLinear.SetShape/MatVecTernary`. Use spans for all buffers.
- Picowal PR78 facades: `Storage.Ready/IsUserPack`,
  `Query.BuildLookupFilter/BuildManyToManyMap`, and
  `Search.Clear/UpsertText/Delete/IndexPack/QueryText/SetVector/QueryHybrid/
  Result/Score/Plan/SetSemanticWeight`.
- `Utf8Writer.*`, `Utf8Reader.*`, `Json.*`, `Xml.*` — text/binary builders.
- `Random.U32(seedReg)`, `Queue.Enqueue/Dequeue/Depth(...)`.

## Output format

- Emit **only** the PicoScript program — no prose, no markdown fences, unless
  the user asks for an explanation.
- Use exactly one surface style for the whole program.
- Keep programs minimal and deterministic; prefer the constructs above.
────────────────────────────────────────────────────────────────────────────

## Worked example — the same program in all four styles

Sum 1..10, skipping multiples of 3, then print the total (→ `37`).

**c**
```c
int s = 0;
for (i = 1; i <= 10; i++) {
    if (i % 3 == 0) { continue; }
    s += i;
}
print(s);
```

**basic**
```basic
DIM S = 0
FOR I = 1 TO 10
    IF I MOD 3 = 0 THEN
        SKIP
    ENDIF
    S += I
NEXT
PRINT S
```

**python**
```python
s = 0
for i in range(1, 11):
    if i % 3 == 0:
        continue
    s += i
print(s)
```

**english**
```text
Set s to 0.
For each i from 1 to 10:
    If i modulo 3 is 0:
        Skip.
    Increase s by i.
Print s.
```

## Validating generated code

Anything the model emits can be checked deterministically with the toolchain:

```bash
# compile + run a generated program (style = c | basic | python | english).
# --print shows the numeric PRINT output; --lang forces the frontend.
python picoscript_build.py run path/to/program.<ext> --lang <style> --print

# the same compiler in the browser / Node — picoc_compile.js emits one hex word
# per line, picovm_run.js executes them and prints REGS / OUT:
node vm/picoc_compile.js <style> < program.txt | node vm/picovm_run.js
```

If a program compiles and runs, it is valid PicoScript. For synthetic-data
generation, generate the **same** algorithm in two or more styles and assert the
VM outputs are identical — that is exactly how `tests/test_pipeline.py` proves
cross-style parity, and it is a strong correctness signal for fine-tuning data.

## Keeping this prompt in sync with the compiler

- **Constructs / operators:** mirror the parity tables in
  `LANGUAGE_SPEC.md` and `docs/picoscript-language-editor.md`. Those tables and
  this prompt are generated/checked against the live gallery in
  `gen_playground.py` (`CONSTRUCTS`), which compiles and runs every snippet in
  all four styles on every build.
- **Host namespaces:** the source of truth is `HOST_HOOK_CODES` in
  `picoscript_lang.py`, but only list a namespace here once it is actually
  dispatched by `HostApi.call` in `picoscript_vm.py` and mirrored in the JS/C
  runtimes where parity is required. Hooks present in the table but not wired
  should be documented as planned, not offered as working examples.
- **Extended hook codes:** hooks may use codes above `0xFF`. The lowerer emits
  `EXT_HOST_HOOK_BASE | (code & 0xFFF)` and the VM decodes the 12-bit extended
  value. `Compress.*` (0x0100+) uses this path.

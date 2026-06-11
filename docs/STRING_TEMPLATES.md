# Strings, numbers & templates

Arena-backed string/number primitives and an AOT-compiled templating engine —
the "holes rendering" picowal/picoweb did by hand, now first-class in PicoScript.
All are host hooks (no compiler/frontend change, byte-identical bytecode), and
all run byte-for-byte identically on the Python and JS VMs.

> Status: a string is a **span** into the arena; results bump-allocate as new
> spans. The whole family — `Span.*`, `String.*`, `Number.*`, `Maths.*`,
> `Compress.*`, `Crypto.Sha256`, `Html.*`, `Http.*` and `Template.*` — now runs
> byte-for-byte identically on **all five runtimes**: the Python VM, the JS VM,
> the portable C VM (`vm/picovm.c`, which carries a span table + bump arena
> `pv_ctx.span_ptr/span_len/arena_top`), and — skipping the bytecode VM entirely —
> the two transpilers `lower_to_c` (native C) and `lower_to_js` (native JS). The
> compilers lower each op to a first-class code-keyed host call (`pv_host2` in C,
> `rt.host(code,…)` delegating to the shared JS host), so there is one
> implementation per language and zero divergence. Verified by
> `tests/test_native_toc.py` (four runtimes from one source) and
> `tests/test_examples_parity.py` (the `examples/*.pc` demos on all five).

## `String.*` (0x80–0x8C)

| Method | Sig | Notes |
|---|---|---|
| `String.Length(s)` | span → int | byte length |
| `String.Concat(a, b)` | span,span → span | new arena span |
| `String.Substring(s, start)` | span,int → span | from `start` to end |
| `String.IndexOf(hay, needle)` | span,span → int | first index or −1 |
| `String.StartsWith(s, p)` / `EndsWith(s, p)` | span,span → 0/1 | |
| `String.ToUpper(s)` / `ToLower(s)` | span → span | ASCII only |
| `String.Trim(s)` | span → span | strips ` \t\r\n` |
| `String.SetReplace(repl)` ; `String.Replace(hay, needle)` | → span | 2-call (the host ABI is 2-in/1-out); replaces all `needle` with the pending `repl` |

## `Number.*` (0x90–0x9A)

`Abs`, `Min`, `Max` (integer); `Floor`/`Ceiling`/`Round` (identity for ints);
`Parse(span)` → int; `ToString`/`ToHex`/`ToOctal`/`ToBinary(int)` → arena span.
These are the int↔string conversions templating needs for `{{count}}`-style holes.

## `Template.*` (0x7A–0x7B) — AOT compiled at *save* time

A template is a **card in walfs**, compiled **once at save time** and stored as a
compact plan; rendering just executes the plan — no JIT parsing on the hot path.

```
plan = Template.Compile(source_span)     // at SAVE time -> store `plan` in a card
out  = Template.Render(plan, model_span) // at RENDER time -> fast, no parse
```

* **Source**: literal text, `{{key}}` holes, sections `{{#key}}…{{/key}}` /
  `{{^key}}…{{/key}}` (inverted), and **iteration** `{{#each list}}…{{/each}}`.
  Sections and loops **nest**; keys are whitespace-trimmed.
* **List model**: indexed flat keys — `list.0.name=…`, `list.1.name=…` for object
  lists, `list.0=…` for scalar lists. Inside `{{#each list}}`, `{{name}}` resolves
  to `list.<i>.name` and `{{.}}` to the scalar item `list.<i>`.
* **Plan** (the AOT artifact, stored in the card): a byte stream —
  `0x01 LEN_HI LEN_LO <bytes>`=literal, `0x02 KEYLEN <key>`=hole,
  `0x03/0x04 KEYLEN <key>`=section/inverted, `0x06 KEYLEN <list>`=each,
  `0x05`=block end.
* **Model**: a `key=value` (newline-separated) span. Missing keys render empty.

Example: `Render(Compile(b"{{#each row}}<td>{{.}}</td>{{/each}}"), b"row.0=A\nrow.1=B")`
→ `<td>A</td><td>B</td>`.

### Roadmap
- Partials (`{{>name}}` including another compiled template).
- Sourcing the model directly from a walfs card's fields (render a card via a
  template — picowal's schema-driven SSR, but data-driven).
- ~~Native `toC`/`toJS` lowering~~ — **done**: `Template.*`/`Http.*`/`Compress.*`/
  `Crypto.Sha256`/`Html.*` are native on the C interpreter and both transpilers.
  ARMv8 SHA-2 / NEON acceleration of `Crypto.Sha256` (the scalar core is in place)
  is the one remaining hardware-validated optimization.

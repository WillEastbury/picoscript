# Strings, numbers & templates

Arena-backed string/number primitives and an AOT-compiled templating engine —
the "holes rendering" picowal/picoweb did by hand, now first-class in PicoScript.
All are host hooks (no compiler/frontend change, byte-identical bytecode), and
all run byte-for-byte identically on the Python and JS VMs.

> Status: interpreter-level (Python + JS), like the `Span`/`Utf8Writer`/`Json`/
> `Xml` family. A string is a **span** into the arena; results bump-allocate as
> new spans. Native `toC` support is the cross-cutting follow-on (it needs the
> span/string model ported to the C runtime, which currently has no span table).

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

* **Source**: literal text, `{{key}}` holes, and **sections** `{{#key}}…{{/key}}`
  (render the body when `key` is non-empty) / `{{^key}}…{{/key}}` (inverted —
  render when empty/absent). Sections **nest**. Keys are whitespace-trimmed.
* **Plan** (the AOT artifact, stored in the card): a byte stream —
  `0x01 LEN_HI LEN_LO <bytes>`=literal, `0x02 KEYLEN <key>`=hole,
  `0x03 KEYLEN <key>`=section, `0x04 …`=inverted section, `0x05`=section end.
* **Model**: a `key=value` (newline-separated) span. Missing keys render empty.

Example: `Template.Compile(b"Hi {{#vip}}*{{/vip}}{{name}}!")` then
`Render(plan, b"vip=1\nname=Bob")` → `Hi *Bob!`.

### Roadmap
- Iteration over lists (`{{#each}}` with a list/array model) and partials
  (`{{>name}}` including another compiled template).
- Sourcing the model directly from a walfs card's fields (render a card via a
  template — picowal's schema-driven SSR, but data-driven).
- Native `toC` lowering of the span/string model (the cross-cutting "native"
  follow-on; see `SYSTEMS_LANGUAGE.md`).

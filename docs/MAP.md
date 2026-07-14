# Map — first-class dictionary primitive

`Map` is a host-managed dictionary available in every PicoScript dialect and VM.
The VM stays a deterministic 32-bit integer machine; the map lives host-side and is
referenced by an integer **handle**. Keys and string values are **byte spans**
(the same representation every VM already uses for strings).

## Calling convention (why the active-handle model)
The PicoScript host-call ABI passes **two inputs** (`rs1`, `rs2`) plus **one result**
(`rd`). A 3-input operation such as `PutSS(handle, key, value)` does not fit. Rather
than add a non-uniform multi-arg convention across every dialect/compiler/VM, `Map`
uses an **active-handle** model: `Map.New()` / `Map.Use(h)` select the *active* map,
and every other op acts on it. Every method is therefore ≤2 args and lowers through
the existing generic host-call path — **no compiler changes in any dialect**.

## Types (v1)
- **Keys:** `int`, `string` (byte span), `hash` (FNV-1a of a span — just an `int` key
  produced by `Map.Hash`).
- **Values:** `int`, `string` (byte span), or `null`.
- **Enumeration:** insertion order (deterministic across all VMs).
- **v2 (deferred):** `float` (IEEE-754 in int32) and `object` (descriptor handle) values.

## Determinism
- Handles are allocated monotonically per run (1-based), so cross-VM output is
  bit-identical.
- **FNV-1a** is fixed for all implementations: offset basis `0x811c9dc5`, prime
  `0x01000193`, 32-bit, result returned as a signed int32.

## API (namespace `Map`, hooks `0x0320`–`0x033A`)
| Method | Args | Result | Notes |
|--------|------|--------|-------|
| `New()` | – | handle | creates empty map, sets it active |
| `Use(h)` | h | – | select active map |
| `Free(h)` | h | – | release; clears active if it was `h` |
| `Clear()` | – | – | empty the active map |
| `Count()` | – | n | entry count |
| `Hash(span)` | span | int | FNV-1a 32-bit |
| `PutII(k,v)` | k,v | – | int→int |
| `GetII(k)` | k | v | 0 if absent (host status set) |
| `HasI(k)` | k | 0\|1 | |
| `DelI(k)` | k | – | |
| `PutIS(k,vSpan)` | k,span | – | int→string |
| `GetIS(k)` | k | span | empty span if absent |
| `PutNullI(k)` | k | – | int→null |
| `IsNullI(k)` | k | 0\|1 | |
| `PutSI(kSpan,v)` | span,v | – | string→int |
| `GetSI(kSpan)` | span | v | |
| `HasS(kSpan)` | span | 0\|1 | |
| `DelS(kSpan)` | span | – | |
| `PutSS(kSpan,vSpan)` | span,span | – | string→string |
| `GetSS(kSpan)` | span | span | |
| `PutNullS(kSpan)` | span | – | string→null |
| `IsNullS(kSpan)` | span | 0\|1 | |
| `KeyAt(i)` | i | int | int/hash key at index |
| `KeySpanAt(i)` | i | span | string key at index |
| `ValAt(i)` | i | int | int value at index |
| `ValSpanAt(i)` | i | span | string value at index |
| `ValIsSpan(i)` | i | 0\|1 | value is a string span |

`hash` keys reuse the int-key methods with `Map.Hash(span)` as the key.

## Example (English dialect)
```
Set orders to Map.New().
Map.PutSI("qty", 42).
Map.PutSI("age", 7).
Print Map.GetSI("qty").          ' -> 42
Print Map.Count().               ' -> 2
Print Map.ValAt(0).              ' -> 42  (insertion order)
```

## Implementation status
| Target | Status |
|--------|--------|
| Canonical hook table (`picoscript_lang.py` → `pico_hooks.js`/`.h`) | ✅ |
| JS reference VM (`vm/picovm.js`) | ✅ (`tests/test_map_hooks.js`) |
| Python reference VM (`picoscript_vm.py`) | ✅ (`tests/test_map_hooks.py`) |
| C VMs (`picoscript/vm`, `picoweb`, `pios`) | ✅ (native C diff — bit-identical to JS/Python) |
| C# workflow host + oracle (integer subset) | ✅ (`developercli/workflow` differential `map_int`) |
| `BareMetal.PicoScript` bundle | ✅ (`BareMetalPicoScript.test.js`) |
| WEB header dicts + round-trip | ✅ (WorkflowPico WEB → request Map + Http.Request; FlowCanvas headers editor) |

## WEB integration (P5)
`Http.Request(method, urlSpan, reqHeadersMap, bodySpan) -> resp`,
`Http.RespStatus/RespHeaders/RespBody`. Request/response headers are
`Map<string,string>` handles; the workflow `WEB` step lowers to real hook calls
(replacing today's comment), so it executes on transport-capable hosts and
round-trips through cross-language translation.

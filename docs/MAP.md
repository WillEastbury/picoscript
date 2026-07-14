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

## Parsing: string/bytes → structured Map
Complementing the `Json.*` writer + `Utf8Reader` scanner, high-level deserializers
turn a string/bytes into a structured `Map` (the "structured object"):

| Hook | Args | Result | Notes |
|------|------|--------|-------|
| `Json.Parse(span)` | jsonSpan | mapHandle | flat JSON object → Map (also sets active) |
| `Binary.ParseCard(span)` | psc1Span | mapHandle | PicoBinarySerializer PSC1 card → Map |
| `Binary.SerializeCard()` | – | span | active Map → PSC1 card (keys sorted) |
| `Binary.ParseEntity(blob, schema)` | blobSpan, schemaMap | mapHandle | BSO1 (BareMetal.Binary) entity → Map |
| `Binary.SerializeEntity(data, schema)` | dataMap, schemaMap | blobSpan | Map → BSO1 entity (signed if a key is set) |
| `Binary.SetKey(span)` | keySpan | – | HMAC-SHA256 signing key (empty = unsigned) |
| `Binary.Verify(blob)` | blobSpan | 0\|1 | HMAC check with the set key |

`Json.Parse` decodes scalar values — string (`PutSS`), integer number (`PutSI`,
floats truncated), `true`/`false` (`PutSI` 1/0), `null` (`PutNullS`) — and captures
nested objects/arrays as their **raw source substring** (a string value), so the
scan is deterministic and identical on every VM. The byte scanners are replicated
verbatim in `picovm.js`, `picoscript_vm.py` and `picovm.c` (all three C VMs), so a
parsed Map is bit-identical everywhere.

```
Set cfg to Json.Parse("{\"qty\":42,\"name\":\"abc\",\"ok\":true}").
Print Map.GetSI("qty").       ' -> 42
Print Map.GetSI("ok").        ' -> 1
```

### BSO1 (BareMetal.Binary) — schema-driven, signed
BSO1 is the BareMetalJsTools entity format: little-endian, fixed-layout per a
**schema**, and HMAC-SHA256 **signed**. The schema is supplied as an **ordered Map**
(field name → wireType code; add `256` to the code to mark a field nullable; an
optional `:version` pseudo-field sets the header schema version). Reading produces a
result Map of field name → value; writing takes a data Map + schema Map and signs
with the key set via `Binary.SetKey`. 64-bit / float / temporal wireTypes are stored
as their **raw little-endian bytes** (a string value) — lossless; the program decodes
further if needed. The reader/writer are byte-compatible with
`BareMetalJsTools/src/BareMetal.Binary.js` (verified incl. the HMAC signature).

wireType codes: `1 Bool, 2 Byte, 3 SByte, 4 Int16, 5 UInt16, 6 Int32, 7 UInt32,
8 Int64, 9 UInt64, 10 Float32, 11 Float64, 12 Decimal, 13 Char, 14 String, 15 Guid,
16 DateTime, 17 DateOnly, 18 TimeOnly, 19 DateTimeOffset, 20 TimeSpan, 21 Identifier,
22 Enum`.

```
Binary.SetKey("my-hmac-key").
Set schema to Map.New(). Map.PutSI("Qty", 6). Map.PutSI("Sku", 14).   ' Int32, String
Set data to Map.New(). Map.PutSI("Qty", 42). Map.PutSS("Sku", "ABC").
Set blob to Binary.SerializeEntity(data, schema).
Print Binary.Verify(blob).                    ' -> 1
Set row to Binary.ParseEntity(blob, schema).
Print Map.GetSI("Qty").                       ' -> 42
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

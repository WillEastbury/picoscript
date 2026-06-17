# PicoScript Language Extensions for PicoForge Web Framework

## Context

PicoForge is a metadata-driven web application runtime being rewritten from Python/FastAPI 
into PicoScript, hosted on PicoWeb with PicoWAL storage and BareMetalJsTools frontend. 
The server-side handlers will be PicoScript compiled to bytecode (VM) and lowered to native C 
via `lower_to_c()`. 

Four language gaps block this. All changes must maintain **five-path parity** 
(Python VM, JS VM, C VM, lower_to_c, lower_to_js) and pass existing tests plus new ones.

---

## GAP 1: Function Parameters + Local Scope (CRITICAL)

### Current behaviour
- `def name():` — parameterless subroutines only
- All variables share one global scope
- `picoscript_python.py:407-413` parses `def name(): ...` with empty parens

### Required behaviour
```python
def handle_request(method, path, body):
    status = 200
    if method == "POST":
        result = Storage.AddCard(0, body)
    else:
        result = Storage.ReadCard(0, path)
    return result

handle_request(Req.Method(), Req.Path(), Req.BodySpan())
```

### Implementation guide

1. **Python frontend** (`picoscript_python.py`):
   - Extend `parse_def()` to parse comma-separated parameter names inside parens
   - Store param names on the `Sub` AST node (add `params: List[str]` field)
   - Extend call-site parsing to pass argument expressions

2. **BASIC frontend** (`picoscript_basic.py`):
   - Extend `SUB name(a, b, c)` / `FUNCTION name(a, b, c)` parsing
   - Same `Sub` node with params

3. **C frontend** (`picoscript_cfront.py`):
   - Already has function syntax; extend if needed

4. **Lowerer** (`picoscript_basic.py` Lowerer class or shared):
   - On `Sub` with params: allocate fresh VRegs for each param (NOT pinned/global)
   - On call site: evaluate argument expressions, emit `mov` from arg VRegs into 
     the callee's param VRegs before `call`
   - Local variables declared inside a `def` body should use non-pinned VRegs 
     (scoped to that function), not global pinned regs
   - The `ret` instruction should pass back a value via `ctx->retval`

5. **IL** (`picoscript_il.py`):
   - `call` already exists. Extend to support passing arguments via registers 
     (convention: first N regs after the call-target label are params)
   - Or add an explicit `arg` instruction

6. **Backends**:
   - `lower_to_c`: local VRegs already emit as function-local `int64_t` vars — 
     just ensure param VRegs are emitted as function parameters or initial locals 
     loaded from a calling convention
   - `lower_to_js`: same pattern — function args become JS function params
   - Bytecode VM: pass args via register slots (document the convention)

7. **Tests**: 
   - Function with 0, 1, 2, 3 params
   - Recursive function (factorial)
   - Local vars don't pollute global scope
   - Return value from function
   - All must pass on all 5 paths

---

## GAP 2: Collection Iteration (CRITICAL)

### Current behaviour
- `for i in range(n):` — numeric range only
- `picoscript_python.py:381-399` hard-codes `range(...)` keyword

### Required behaviour
```python
# Iterate over Storage query results
count = Storage.QueryCard(0, "status=open")
for i in range(count):
    card = Storage.QueryResult(i)
    name = Storage.GetField(card, "name")
    Json.Key("name")
    Json.Str(name)

# Iterate over split string
parts = String.Split(path, "/")
for i in range(String.Length(parts)):
    segment = Span.Get(parts, i)
    ...
```

### Implementation guide

The pattern is: **a host call returns a count, then index-access in a `range()` loop.**
This already works with the existing `for i in range(expr):` syntax if the expression
can be an arbitrary expression (not just a literal). 

**Check**: does `for i in range(Storage.QueryCard(0, "x")):` already parse correctly?
The `parse_for` method calls `parse_expr()` for range args, and `parse_atom` handles 
`Ns.Method(...)` calls — so this may **already work**. Verify with a test.

If it works, the remaining need is a higher-level `for item in collection:` sugar:

1. **Python frontend**: 
   - When `for x in expr:` is seen (where expr is NOT `range(...)`):
   - Desugar to: `_count = Span.Len(expr); for _i in range(_count): x = Span.Get(expr, _i)`
   - Or emit a `ForEach` node that the Lowerer handles with this pattern

2. **Lowerer**:
   - `ForEach` with a non-numeric source → emit: call source (get span), 
     emit Span.Len, emit a counted loop with Span.Get per iteration

3. **Backends**: No changes needed — it desugars to existing IL ops.

4. **Tests**:
   - `for i in range(Storage.QueryCard(...)):` with QueryResult indexing
   - `for part in String.Split(s, ","):` iteration
   - `for card in Storage.QueryCard(...):` (sugar form)
   - Empty collection (0 iterations)
   - All 5 paths

---

## GAP 3: Error Handling — try/except (IMPORTANT)

### Current behaviour
- `raise` IL op exists (`picoscript_il.py:1214`: `pv_raise(ctx, imm)`)
- No `try/except` syntax in any frontend
- No catch/handler mechanism in the Lowerer

### Required behaviour
```python
def get_entity(entity_id, item_id):
    try:
        card = Storage.ReadCard(entity_id, item_id)
        if card == 0:
            raise 404
        return card
    except:
        Resp.Status(404)
        Json.BeginObject()
        Json.Key("error")
        Json.Str("not_found")
        Json.EndObject()
        Resp.End()
```

### Implementation guide

1. **Python frontend**:
   - Parse `try:` block + `except:` block + optional `finally:` block
   - `raise expr` as a statement (already partially handled?)

2. **AST**: Add `TryExcept(try_body, except_body, finally_body)` node

3. **Lowerer**:
   - Emit a "try-entry" marker that sets a handler label
   - On `raise`: jump to handler label (or use the existing `raise` op)
   - Handler label = except body
   - Finally: emit after both paths

4. **IL**: 
   - Add `try_begin(handler_label)` and `try_end` instructions
   - `raise` already exists — it should jump to the nearest handler
   - Or: use `cmpbr` on a status register after each host call 
     (simpler, no stack unwinding needed)

5. **Backends**:
   - C: `if (ctx->error) goto handler;` after host calls, or use setjmp/longjmp
   - JS: try/catch maps directly
   - Bytecode VM: error flag + handler label stack

6. **Simpler alternative** (recommended for v1):
   - Don't implement full exceptions
   - Instead: host calls that fail set `ctx->error` flag
   - Add `if_error:` / `on_error goto label` syntax
   - PicoScript checks error after each host call: `if ctx->error: goto handler`
   - This avoids stack unwinding complexity

7. **Tests**:
   - try with no error → except not executed
   - try with raise → except executed
   - Nested try/except
   - All 5 paths

---

## GAP 4: New Host Hooks (IMPORTANT)

### 4a. Base64 Encode/Decode (needed for JWT)

Add to `Crypto` or `String` namespace:
```
String.Base64Encode(span) → span    # or Crypto.Base64Encode
String.Base64Decode(span) → span    # or Crypto.Base64Decode
String.Base64UrlDecode(span) → span # JWT uses URL-safe base64
```

- Assign hook codes in the `0x70xx` or `0x61xx` range
- Implement in C runtime (picovm.h), JS runtime, Python runtime
- Pure/deterministic — can be parity-tested

### 4b. DateTime Operations (needed for temporal filters)

Add to `DateTime` namespace (host-injected, non-deterministic):
```
DateTime.Now()              → int64 (unix millis) [already planned]
DateTime.Parse(iso_str)     → int64 (unix millis from ISO 8601 string)
DateTime.Format(millis)     → span  (ISO 8601 string)
DateTime.AddDays(millis, n) → int64
DateTime.DiffDays(a, b)     → int64
DateTime.DayOfWeek(millis)  → int (0=Mon..6=Sun)
DateTime.Year(millis)       → int
DateTime.Month(millis)      → int
DateTime.Day(millis)        → int
```

- `Parse`/`Format`/`AddDays`/`DiffDays`/`DayOfWeek`/`Year`/`Month`/`Day` are 
  pure (deterministic given input) — can be parity-tested
- `Now` is non-deterministic (host-injected, already documented)

### 4c. Req.Param (route parameter extraction)

```
Req.Param(index) → span    # 0-based path segment: /api/orders/123 → Param(2) = "123"
Req.ParamCount() → int     # number of path segments
```

- Simpler than named params — the PicoWeb route matcher extracts segments
- Assign hook codes in the Req range

---

## Constraints

- **Five-path parity must be maintained** — `tests/test_pipeline.py`, 
  `tests/test_io_hooks.py`, `tests/test_examples_parity.py` must all pass
- **Bytecode format must remain stable** for existing programs
- **lower_to_c must produce valid C** that compiles with gcc/clang -Wall -Werror
- **lower_to_js must produce valid ES2020** JavaScript
- **Do not break the `vm/picoc.js` frontend** — it must stay byte-identical for 
  programs that don't use new features (backward compatible)
- **Add tests for every new feature** in the multi-path test harness

## Priority order
1. Function parameters (blocks everything else)
2. Collection iteration (blocks CRUD handlers)  
3. Base64 + DateTime host hooks (blocks auth)
4. Error handling (blocks graceful failures)

---

## Appendix: What Already Works (no changes needed)

| Capability | Namespace/Method | Notes |
|-----------|-----------------|-------|
| String manipulation | `String.*` (13 methods) | Concat, Split, Join, Substring, Replace, Trim, Case, IndexOf, StartsWith, EndsWith |
| JSON building | `Json.*` (10 methods) | BeginObject/EndObject, Key, Str, Int, Bool, Null, BeginArray/EndArray, Raw |
| JSON parsing | `Http.ParseJson` | JSON → dotted-path model for Template `{{#each}}` |
| HTTP request reading | `Req.*` (11 hooks) | Method, Path, Header, Body, Principal, BodySpan, BodySlice, BodyLen |
| HTTP response writing | `Resp.*` (14 hooks) | Status, Header, Write, Flush, End, Seal, Respond, Continue, EarlyHints, Upgrade |
| HTTP response framing | `Net.*` (5 methods) | Status, Type, Header, Body, Close |
| HTTP utilities | `Http.*` (8 methods) | ParseQuery, ParseForm, ReadHeader, ReadBody, EncodeJson, ParseJson, GenerateHeaders, GenerateResponse |
| Storage CRUD | `Storage.*` (21 methods) | AddCard/ReadCard/EditCard/DeleteCard/QueryCard/PatchCard, GetField/SetField, Schema, UsePack |
| Template rendering | `Template.*` (2 methods) | Compile, Render (Mustache-style with `{{#each}}`) |
| HTML escaping | `Html.*` (2 methods) | Encode, Decode |
| Crypto | `Crypto.Sha256`, `Crypto.HmacSha256` | Sufficient for JWT HMAC validation |
| Number ops | `Number.*` (11 methods) | Parse, ToString, Abs, Min, Max, Round, Floor, Ceiling |
| Memory | `Memory.*` (8 methods) | Arena alloc, Get/Set, Peek/Poke |
| Span | `Span.*` (5 methods) | Make, Slice, Get, Len, Materialize |
| Compression | `Compress.*` | PicoCompress, Brotli, Gzip, Deflate |
| Control flow | if/elif/else, while, for range | All frontends |
| Subroutines | `def name()` / `GOSUB` | Parameterless (being extended) |
| Expressions | `+ - * / % == != < > <= >= and or not` | Full operator set |
| CORS headers | `Resp.Header(name, value)` | Just emit the right Access-Control-* headers |
| Dict/object access | `Storage.GetField/SetField` | Cards ARE objects — no dict type needed |

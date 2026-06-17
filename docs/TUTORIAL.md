# PicoScript tutorial

This tutorial is the shortest path from "I have the page open" to "I can write,
run, and check a PicoScript program."

PicoScript has four surface languages:

| Style | File extension | Best for |
|-------|----------------|----------|
| C-style | `.pc` | compact systems-style code |
| BASIC | `.pbas` / `.bas` | readable block code and device/UI DSLs |
| Python-style | `.ppy` | indentation-based code |
| English | `.eng` | plain imperative sentences |

All four styles compile through the same PicoIL pipeline. The same program should
produce the same bytecode and the same output on every runtime path.

## 1. Open the portal

Use the live site:

<https://willeastbury.github.io/picoscript/>

The top tabs are:

- **Guide** — examples for each language construct.
- **Playground** — write, compile, run, and step programs.
- **Reference** — method lists and implementation notes.

For a first run, open **Guide**, pick any card, and click **Edit in Playground**.
Then click **Compile & Run**. The output pane shows the raw packed integer chunks,
and for printable output it also shows a `text:` line.

PicoWAL persistence is on by default in the playground. `Storage.*` reads and
writes use the browser's localStorage-backed card store, so cards survive resets
and reloads until you click **Clear** in the TCP/HTTP/Card panels.

## 2. Write your first program

Open **Playground**, choose **C-style**, paste this:

```c
Io.Write("Hello from PicoScript");
```

Click **Compile & Run**. Success looks like:

```text
compiled ...
output: [...]
text: "Hello from PicoScript"
```

The integer list is the exact VM output representation. `text:` is a convenience
view for printable bytes.

## 3. The same idea in BASIC

Switch the language dropdown to **BASIC** and paste:

```basic
PRINT "Hello from BASIC"
```

Click **Compile & Run**. The text output should be:

```text
Hello from BASIC
```

BASIC has idiomatic forms for several namespaces. Prefer `PRINT`, `STORE`,
`LOAD`, `GPIO`, `UI`, and `EVENT` when writing BASIC examples; use dotted
`Namespace.Method(...)` calls when you are writing C-style code.

## 4. Strings are spans

Most library calls take and return **spans**: handles to bytes in the VM arena.
String literals automatically become spans, so you can write readable code:

```c
int s = "Ada";
int up = String.ToUpper(s);
Io.Write(up);
```

Expected text:

```text
ADA
```

When you need exact bytes, use `Memory.Set` + `Span.Make`, but prefer string
literals in examples unless byte-by-byte construction is the point.

## 5. Compression round-trip

PicoScript includes two in-runtime codecs:

- `Compress.PicoCompress` / `PicoDecompress` — the small deterministic
  `picocompress` codec.
- `Compress.BrotliCompress` / `BrotliDecompress` — real Brotli output that a
  browser or zlib can decode.

Try a Brotli round-trip:

```c
int text = "brotli in, brotli out";
int packed = Compress.BrotliCompress(text);
int plain = Compress.BrotliDecompress(packed);
Io.Write(plain);
```

Expected text:

```text
brotli in, brotli out
```

The compressed bytes are deterministic across the Python VM, JS VM, C VM,
native C transpiler, and native JS transpiler. They are not intended to match
Google's encoder byte-for-byte; they are intended to be valid Brotli and
identical across PicoScript runtimes.

## 6. Render a tiny remote UI

The `Ui.*` namespace builds a retained scene tree. `Ui.Serialize` turns it into
PicoWire bytes for a remote client. In BASIC:

```basic
DIM WIN = UI WINDOW "Login"
UI SIZE WIN = 220, 130
DIM GO = UI BUTTON WIN "Sign in"
UI POS GO = 70, 86
UI SETID GO = 3
DIM WIRE = UI SERIALIZE WIN
Io.Write(WIRE)
```

In the portal, use the Remote UI panel to render the wire and post click events
back through `Event.*`. A real host can send the same PicoWire bytes over a
socket or another transport.

## 6b. Stream HTML without a full template

For dynamic handlers that stream a page, use `Utf8Writer` with `TextRender.*`.
It gives you escaped text/attributes, raw tag fragments, `<br/>`, and simple
hole rendering from `key=value` model spans:

```c
int w = Utf8Writer.New(3000, 512);
TextRender.Open(w, "body");
TextRender.Attr(w, "class=main");
TextRender.OpenEnd(w);
TextRender.Text(w, "<safe>");      // &lt;safe&gt;
TextRender.Br(w);                  // <br/>

int model = "name=Ada";
TextRender.Hole(model, "name");    // Ada

TextRender.Close(w, "body");
Io.Write(Utf8Writer.ToSpan(w));
```

Use `Template.Compile/Render` when the page shape is mostly static and saved in
a card; use `TextRender.*` when a handler is incrementally streaming HTML.

## 7. Run examples from the command line

The browser is easiest for learning. The CLI is better for repeatable checks.

```powershell
cd C:\source\picoscript

# Run on the Python reference VM.
C:\Python313\python.exe picoscript_build.py run examples\text_tools.pc --print

# Show bytecode.
C:\Python313\python.exe picoscript_build.py emit examples\text_tools.pc --as bytecode --hex

# Emit native C.
C:\Python313\python.exe picoscript_build.py emit examples\web_template.pc --as c -o out.c
```

The CLI prints output as packed integer chunks. Use the portal when you want the
decoded printable text view.

## 8. What to check before changing language behavior

PicoScript's main guarantee is parity. If you change syntax, lowering, a host
namespace, or a runtime, run the relevant parity tests:

```powershell
cd C:\source\picoscript
C:\Python313\python.exe tests\test_pipeline.py
C:\Python313\python.exe tests\test_examples_parity.py
C:\Python313\python.exe tests\test_picobrotli.py
C:\Python313\python.exe tests\test_picocompress.py
```

For a full local check:

```powershell
Get-ChildItem tests\test_*.py | ForEach-Object {
    & C:\Python313\python.exe $_.FullName
}
```

If those pass, your change is much more likely to behave the same in the browser,
the Python reference VM, the C VM, and both native transpilers.

## 10. Inbound HTTP/TCP descriptor cards

The portal's **HTTP** and **TCP** panels write inbound data into PicoWAL before
running your program. Simple examples can read these low-card mirrors:

| Card | Meaning |
|------|---------|
| `0` | total inbound byte length |
| `1` | HTTP method code (`1=GET`, `2=POST`, `3=PUT`, ...) |
| `2` | body/frame length |
| `3` | simple byte checksum |
| `4` | path length |
| `5` | query-string length |
| `6` | body mirror length |
| `10..19` | small query-string mirror |
| `20..31` | small body/TCP-frame mirror |

The simulator also writes larger visual/debug mirrors at higher internal card
addresses, but the `0..31` range is the easiest one to read from PicoScript's
legacy `Storage.Load(tenant, pack, card, out)` form.

Use the Guide cards **HTTP request: parse query + body** and **TCP stream: parse
parameter frame** as copyable examples. They show the same pattern the simulator
uses:

1. Load the length card.
2. Load each byte into memory.
3. Wrap the bytes with `Span.Make`.
4. Parse with `Http.ParseQuery` or `Http.ParseForm`.
5. Respond with `Net.Status` and `Io.Write`.

## 11. Large cards: slice, don't load

Before the large-card API, it helps to separate two storage shapes:

- **Small structured cards**: schema-backed records, good for active-record dot
  access.
- **Large blob/dataset cards**: byte ranges, good for slice/row APIs.

For small schema-backed records, C-style PicoScript now supports active-record
authoring over the existing `Storage.*` hooks:

```c
Storage.UsePack(1);
int id = Storage.AddCard();

Order ord = Storage.GetCard(1, id);
ord.qty = 42;
Storage.SaveCard(ord);

print(ord.qty);
```

`Order` is a source-level schema/type name. The compiler lowers the handle and
dot field operations to `Storage.UsePack`, `Storage.EditCard`,
`Storage.SetField`, and `Storage.GetField`. `Storage.SaveCard(ord)` is currently
a flush/no-op because `SetField` is eager; it keeps the source shape ready for a
future dirty-buffered record implementation.

Query materialization can start simple:

```c
int n = Storage.QueryCards(1, "qty > 40");
for (i = 0; i < n; i++) {
    Order ord = Storage.GetCard(1, Storage.QueryResult(i));
    ord.qty--;
    Storage.SaveCard(ord);
}
```

The lower-level `Storage.SetField(...)` and BASIC `STORE`/`LOAD` forms remain the
escape hatch for schema-less cards and ordinal fields.

Small schema-backed cards can be treated like records. Big cards (datasets,
weights, logs, media) should be treated as byte-addressable blobs. Use the slice
API so a program reads only the bytes it needs:

```c
int card = 7;
int off = 1048576;       // byte offset into a large card
int len = 4096;          // window size

Storage.UsePack(1);
Storage.SetSlice(off, len);
int chunk = Storage.ReadSlice(card);
Io.Write(chunk);
```

The low-level primitives are:

| Method | Meaning |
|--------|---------|
| `Storage.SetSlice(offset, len)` | set the current byte window |
| `Storage.CardLen(card)` | return the blob card length |
| `Storage.ReadSlice(card)` | return the current window as a span |
| `Storage.WriteSlice(card, span)` | patch bytes at the current offset |

The playground simulator keeps a small in-memory blob-card backend so examples
can run locally. PIOS should bind the same hooks to WALFS/SD range I/O, so a
400MB dataset card can be scanned in fixed windows without ever becoming a
400MB VM span.

Typed active-record cards should use dot fields for small structured records;
blob and dataset cards should use slice/row APIs under the hood.

## 12. Stream, request, and event payload slices

The same slice-first rule applies to inbound data. HTTP bodies, TCP/UDP frames,
device streams, and event payloads may be much larger than the bytes a handler
needs. The whole-blob APIs still exist, but handlers can ask for a window.

| Source | Whole blob | Slice window |
|--------|------------|--------------|
| HTTP/TCP request body | `Req.BodySpan(index)` | `Req.SetSlice(offset, len)` then `Req.BodySlice(index)` |
| Stream lease | `Stream.Span(lease)` | `Stream.SetSlice(offset, len)` then `Stream.Slice(lease)` |
| Event payload | `Event.Data(ev)` | `Event.SetSlice(offset, len)` then `Event.DataSlice(ev)` |

Example: handle an event carrying a TCP-style frame and extract just the command:

```c
int ev = Event.Post(2, 99);
int payload = "cmd=PING&n=3";
Event.SetData(ev, payload);

int got = Event.Next();
if (Event.Type(got) == 2) {
    Event.SetSlice(4, 4);
    int cmd = Event.DataSlice(got);
    Io.Write(cmd);        // PING
}
```

Example: read a window from a stream lease:

```c
int dev = Device.Open("udp0", 0);
int s = Stream.Open(dev, 65588);  // RX, 26-byte frame, 1 frame
int lease = Stream.Next(s);

Stream.SetSlice(10, 5);
int part = Stream.Slice(lease);
```

On PIOS, these hooks should map to descriptor/lease range reads rather than
copying the whole request or stream buffer into PicoScript memory.

## 13. Tensor and transformer primitives

PicoScript now has a small inference-kernel surface so an AI harness can be
written once and run on whatever capability the host VM provides: scalar VM,
M33 DSP, desktop SIMD, or a future tensor accelerator.

| Primitive | Use |
|-----------|-----|
| `Tensor.SetShape(rows, cols)` | configure matrix/vector shape |
| `Tensor.DotI8(a, b)` | signed int8 dot product |
| `Tensor.MatVecI8(matrix, vector)` | int8 matrix-vector, returns `span<int32_be>` |
| `Tensor.AddI32`, `MulI32`, `ScaleI32`, `ReluI32` | elementwise FFN/residual helpers |
| `Tensor.RmsNormI32(x, gamma)` | integer RMSNorm-like normalization |
| `Tensor.RoPEI32(x, cosSin)` | pairwise RoPE rotation using Q15 cos/sin |
| `Tensor.SoftmaxI32(logits)` | deterministic Q15 attention weights |
| `Tensor.ArgMaxI32(logits)` | index of the largest int32 |
| `Attention.Scores/Mix/Attend` | streaming attention score/mix helpers |
| `Quant.AbsMax/QuantI8/DequantI8/ApplyScale/GroupScale` | quantization and group-scale helpers |
| `BitLinear.MatVecTernary(weights, act)` | BitNet-style ternary matvec |
| `BitLinear.MatVecBitmap(weights, act)` | bitmap trit rows (`zero_mask` + `minus_mask`) |
| `BitLinear.MatVecBase3(weights, act)` | base-3 packed trit rows |
| `Tokenizer.EncodeBytes/DecodeBytes` | byte-fallback tokenizer baseline |
| `Tokenizer.SetVocab/EncodeTrie/DecodeTrie` | longest-prefix vocab trie baseline |
| `Model.SetConfig/GetConfig/TensorView/ReadTensor/ReadTensorRow` | model metadata and storage-bound tensor views |
| `Kv.WriteK/WriteV/ReadK/ReadV` | KV cache records by layer/position |
| `Sampling.ArgMax/TopK/Temperature` | logits selection helpers |

All buffers are spans. Matrix-vector outputs are int32 values encoded big-endian
in a returned span, so they can feed the next primitive without changing the VM
instruction format.

```c
Tensor.SetShape(2, 4);
int out = Tensor.MatVecI8(weightRows, activation);
int token = Tensor.ArgMaxI32(out);
```

For BitNet-style packed weights:

```c
BitLinear.SetShape(rows, cols);
int out = BitLinear.MatVecTernary(packedTritRows, activationI8);
int out2 = BitLinear.MatVecBitmap(bitmapRows, activationI8);
int out3 = BitLinear.MatVecBase3(base3Rows, activationI8);
```

The reference VM implements deterministic scalar versions. A production host can
bind the same hooks to M33 DSP, AVX2, V3D/QPU, or another accelerator.

Minimal model loop scaffolding:

```c
Tokenizer.EncodeBytes(prompt);
int token = Tokenizer.Token(0);
Tokenizer.SetVocab("hello=100;world=101");
Tokenizer.EncodeTrie("hello world");

Model.SetConfig(1, 128);                 // e.g. hidden_dim
Model.TensorView(3, "2|7|4096|2|4|15");  // pack|card|offset|rows|cols|format
int row = Model.ReadTensorRow(3, 0);

Kv.WriteK((layer << 16) | pos, kSpan);
Kv.WriteV((layer << 16) | pos, vSpan);

int logits = Tensor.MatVecI8(lmHeadRows, hidden);
int next = Sampling.ArgMax(logits);

Attention.SetShape(heads, headDim);
int scores = Attention.Scores(query, kRows);
int weights = Tensor.SoftmaxI32(scores);
int context = Attention.Mix(weights, vRows);
```

## 14. Picowal PR78 facades

PicoScript also exposes language hooks for the newer Picowal host features:

- `Storage.Ready()` and `Storage.IsUserPack(pack)` for disk-only/user-pack
  policy checks.
- `Query.BuildLookupFilter(pack, spec)` and
  `Query.BuildManyToManyMap(mappingPack, spec)` for bounded relation query
  builders.
- `Search.Clear/UpsertText/Delete/IndexPack/QueryText/QueryHybrid/Result/Score/Plan`
  plus `Configure/Compatible/Rebuild`, `SetFacet/Facets/FacetValue/FacetCount`,
  `SetNumber/Range`, `Save/Load`, and `Journal*` mutation hooks as a deterministic
  facade over Picowal host search indexes. The reference VM uses a small
  lexical/vector-signature/facet/range approximation; production hosts can bind
  BM25, ANN, hybrid ranking, semantic rerank callbacks, persistent index segments,
  and append-only search journals behind the same hooks.

## 9. Where to go next

- **Guide tab:** copy and edit examples for loops, branches, HTTP, storage, UI,
  and events.
- **Reference tab:** look up namespace methods and hook codes.
- **`docs/COMPRESS.md`:** codec details.
- **`docs/PICO_UI.md`:** events and remote UI.
- **`docs/PSUNIT.md`:** writing PicoScript tests in PicoScript.

## 14. OS-worker primitives

PicoScript programs can use OS-worker primitives for process lifecycle, timers,
identity, error recovery, and inter-card module switching. The reference VM (Python
and JS) provides deterministic simulators; PIOS binds real OS services behind the
same hooks.

### Process lifecycle and environment

```c
int pid = Process.Self();       // current process id (1 by default)
int ppid = Process.Parent();    // parent process id (0 = root)
int child = Process.Spawn(1024, 42);  // spawn capsule pack=1024 entry=42
int s = Process.Status(child);  // 0=running, 1=exited, 2=faulted
Process.Kill(child);            // terminate a process

Env.Set("MODE", "production");
int val = Env.Get("MODE");      // -> span "production"
int n = Env.Count();            // number of env vars
int k = Env.Key(0);             // key at index 0
```

### Timers and scheduler events

Timer expirations inject `EVENT_TIMER` (type=100) events into the `Event.*` queue.
Use `Scheduler.Tick(ms)` in tests to advance simulated time.

```c
int t1 = Timer.After(1000);   // one-shot: fires after 1000ms
int t2 = Timer.Every(500);    // repeating: fires every 500ms
Timer.Cancel(t2);              // cancel a timer
int ms = Timer.Elapsed();      // simulated monotonic clock

Scheduler.Tick(1500);          // advance time by 1500ms, fire pending timers
int ev = Event.Next();         // dequeue EVENT_TIMER
int type = Event.Type(ev);     // 100 = EVENT_TIMER
int handle = Event.Target(ev); // which timer fired
```

### Identity and capabilities

```c
int name = Principal.Current();     // -> span "anonymous" (default)
int ok = Principal.HasRole("admin"); // 0 or 1
int claims = Principal.Claims();     // -> span "key=value;..."

int has = Capability.Has(8);     // check if STORAGE cap is granted
Capability.Drop(8);             // voluntarily drop a capability
Sandbox.Deny(8);                // irrevocably deny (can't Request back)
```

### Global error handling

When `Error.SetHandler` is set and a VM fault occurs, the VM jumps to the handler
instead of halting. Inspect the fault with `Error.Code/Detail` and recover with
`Error.Resume` or `Error.Clear`.

```c
// Phase 1: global handler (ON ERROR GOTO style)
Error.SetHandler(:handler);
// ... code that might fault ...
Flow.Jump(:done);
:handler
int code = Error.Code();
int detail = Error.Detail();
Error.Clear();
:done
```

### Capsule module switching

```c
int r = Capsule.Call(1024, 1);      // run pack=1024 card=1 synchronously
Capsule.Schedule(1024, 2);          // bind card to future event dispatch
int mod = Capsule.LoadModule(1024, 3);  // load without executing
int result = Capsule.RunModule(mod);     // execute loaded module
Capsule.Jump(1024, 4);              // transfer execution (halts current)
```

## 15. Function parameters and return values

Functions in all 4 frontends accept parameters and return values:

```c
// C frontend
void add(int a, int b) {
    return a + b;
}
int result = add(10, 32);   // result = 42
Io.WriteByte(result);
```

```python
# Python frontend
def add(a, b):
    return a + b
r = add(10, 32)
Io.WriteByte(r)
```

```basic
' BASIC frontend
SUB ADD(A, B)
    RETURN A + B
ENDSUB
LET R = ADD(10, 32)
```

Calling convention: args via `__arg0__`..`__argN__` registers, return via `__ret__`.
Non-recursive calls work with full 5-path parity.

## 16. Error handling (try/except)

Python frontend supports try/except/finally with the Error.* hooks:

```python
try:
    card = Storage.ReadCard(0, 123)
except:
    Resp.Status(404)
    Resp.End()
finally:
    Io.WriteByte(0)
```

The `raise` statement triggers OP_RAISE; the except body checks `Error.Code`.

## 17. Base64, DateTime, Req.Param

```c
// Base64 for JWT
int encoded = Base64.Encode(payload);
int decoded = Base64.Decode(encoded);
int urlDecoded = Base64.UrlDecode(jwt_part);

// DateTime decomposition
int year = DateTime.Year(millis);
int month = DateTime.Month(millis);
int diff = DateTime.DiffDays(a, b);

// Path parameter extraction: /api/orders/123
int count = Req.ParamCount();   // 3
int seg = Req.Param(2);         // "123"
```

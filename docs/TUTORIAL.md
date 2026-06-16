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

## 9. Where to go next

- **Guide tab:** copy and edit examples for loops, branches, HTTP, storage, UI,
  and events.
- **Reference tab:** look up namespace methods and hook codes.
- **`docs/COMPRESS.md`:** codec details.
- **`docs/PICO_UI.md`:** events and remote UI.
- **`docs/PSUNIT.md`:** writing PicoScript tests in PicoScript.

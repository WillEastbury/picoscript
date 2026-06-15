# PicoUI — events + a tiny remote windowing protocol

PicoScript gains two composable capabilities for building reactive, optionally
remoted user interfaces, both built into the runtime (deterministic, byte-identical
on the Python and JS VMs) and capability-gated:

- **`Event.*`** — a reactive event queue (the async/UI dispatch core).
- **`Ui.*`** — a retained scene tree serialized to **PicoWire**, a minimal binary
  windowing protocol a thin remote client renders.

They compose: `Ui.Serialize` produces a wire a client renders; the client sends user
input back as `Event.*` records keyed by control id; the program loops on
`Event.Next()`. Think RDP/X, but tiny and clean.

## Event.* — reactive event queue

A deterministic in-runtime FIFO of events, each a `(type, target, data-span)` record.
A program pulls events and dispatches, mirroring the `Stream.Next` lease pattern.
External sources (browser UI, PIOS timers/IRQs) inject through the same `Post` path,
so an event loop is identical in the sim and on hardware. **Capability: `CAP_EVENT`
(`1<<14`).**

| Hook | Code | In → Out | Effect |
|------|------|----------|--------|
| `Event.Post(type, target)` | `0x0180` | `type, target → ev` | enqueue an event, return its id |
| `Event.Next()`             | `0x0181` | `→ ev` | dequeue the oldest pending event (`0` = empty) |
| `Event.Type(ev)`           | `0x0182` | `ev → type` | event kind |
| `Event.Target(ev)`         | `0x0183` | `ev → target` | control id the event is for |
| `Event.Data(ev)`           | `0x0184` | `ev → span` | attached data span (`0` = none) |
| `Event.SetData(ev, span)`  | `0x0185` | `ev, span → ok` | attach a data span |
| `Event.Count()`            | `0x0186` | `→ n` | pending event count |

A typical event loop:

```c
int e = Event.Next();
while (e != 0) {
    if (Event.Type(e) == 1) { /* a click on */ int id = Event.Target(e); /* ... */ }
    e = Event.Next();
}
```

## Ui.* — retained scene tree + PicoWire

Build a window and controls as a retained tree, then `Ui.Serialize(root)` emits the
PicoWire bytes. **Capability: `CAP_UI` (`1<<15`).** Control kinds: `1=window 2=panel
3=label 4=button 5=textbox 6=checkbox`.

| Hook | Code | In → Out | Effect |
|------|------|----------|--------|
| `Ui.Window(title)`        | `0x0188` | `title-span → node` | create the root window |
| `Ui.Panel(parent)`        | `0x0189` | `parent → node` | container box |
| `Ui.Label(parent, text)`  | `0x018A` | `parent, text → node` | static text |
| `Ui.Button(parent, text)` | `0x018B` | `parent, text → node` | clickable button |
| `Ui.TextBox(parent, text)`| `0x018C` | `parent, text → node` | text field |
| `Ui.Checkbox(parent, text)`| `0x018D`| `parent, text → node` | checkbox + caption |
| `Ui.Pos(node, (x<<16)\|y)` | `0x018E` | `node, xy → ok` | position |
| `Ui.Size(node, (w<<16)\|h)`| `0x018F` | `node, wh → ok` | size |
| `Ui.SetText(node, span)`  | `0x0190` | `node, span → ok` | replace text |
| `Ui.SetId(node, id)`      | `0x0191` | `node, id → ok` | control id (the `Event.Target`) |
| `Ui.SetValue(node, v)`    | `0x0192` | `node, v → ok` | e.g. checkbox state |
| `Ui.Serialize(root)`      | `0x0193` | `root → span` | the PicoWire wire |

```c
int win = Ui.Window("Login");
Ui.Size(win, 220 * 65536 + 130);
int go = Ui.Button(win, "Sign in");
Ui.Pos(go, 70 * 65536 + 86); Ui.SetId(go, 3);
int wire = Ui.Serialize(win);          // hand `wire` to the transport
```

In BASIC the `UI` and `EVENT` keywords give an idiomatic DSL (they shadow the
dotted `Ui.*`/`Event.*` forms, like `GPIO` shadows `Gpio.*`):

```basic
DIM WIN = UI WINDOW "Login"
UI SIZE WIN = 220, 130          ' (w << 16) | h
DIM GO = UI BUTTON WIN "Sign in"
UI POS GO = 70, 86              ' (x << 16) | y
UI SETID GO = 3
DIM WIRE = UI SERIALIZE WIN

DIM E = EVENT NEXT
IF EVENT TYPE E = 1 THEN
    DIM ID = EVENT TARGET E     ' which control was clicked
ENDIF
```

`UI POS`/`UI SIZE` accept either a packed value or a readable `x, y` pair (lowered
to `(x<<16)|y`). Other forms: `UI PANEL/LABEL/TEXTBOX/CHECKBOX`, `UI SETTEXT/SETVALUE`,
`EVENT POST type target`, `EVENT DATA/SETDATA/COUNT`.

## PicoWire wire format

PicoWire deliberately **reuses the canonical PicoSerializer (PSC1) record format**
(`picoserializer.py`/`.js`: `MAGIC 0x50534331` `"PSC1"`, `T_INT=1`, `T_STR=2`,
sorted keys) — the same byte vocabulary as the card data plane, not a private
format. A document is:

```
u16  nodeCount                         (big-endian)
nodeCount × PSC1 record                (pre-order DFS of the scene tree)
```

Each node record carries these fields (PSC1 sorts keys, so the byte order is fixed):

| key | meaning |
|-----|---------|
| `c`  | kind (1..6) |
| `ch` | child count (lets the client rebuild the tree from the pre-order list) |
| `h`  | height |
| `id` | control id |
| `t`  | text (UTF-8) |
| `v`  | value |
| `w`  | width |
| `x`, `y` | position |

A client decodes each record with `PicoSerializer.deserializeCard`, rebuilds the
tree from the `ch` counts, and renders it. `docs/playground.html` ships a reference
remote client (the **Remote UI** tab): it renders the wire as window chrome +
controls and posts clicks/toggles back through `Event.*`.

## Remoting model

```
program ──Ui.*──▶ scene tree ──Ui.Serialize──▶ PicoWire ──transport──▶ client renders
   ▲                                                                        │
   └────────────── Event.Next() ◀── Event.Post(type, controlId) ◀── user input
```

The transport is the host's job (a socket on PIOS, an in-page call in the browser
sim). The scene model, serialization and event queue are all in the runtime, so the
same program drives the browser simulator and a real remote display identically.

## Determinism & security

- The scene tree, serializer and event queue are pure integer/arena logic, so the
  **Python VM and JS VM are byte-identical** (`tests/test_ui.py`, `tests/test_events.py`).
- Both namespaces are **capability-gated** (`CAP_UI`, `CAP_EVENT`): a binding without
  the grant faults (INV-17) rather than spawning windows or draining events.
- The actual pixels/input device and the transport are the only host edges; no
  application algorithm lives in the host.

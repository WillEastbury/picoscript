# PicoScript primitive guide

This guide groups the core runtime primitives by what you are trying to do:
maths, byte arrays, strings, encodings, hashing/crypto, RNG, and streaming text.

PicoScript values are signed 32-bit integers. Text and byte arrays are represented
as **spans**: integer handles pointing at bytes in the arena.

## Maths and integer primitives

| Namespace | Methods | Notes |
|-----------|---------|-------|
| `Math` | `Add`, `Sub`, `Mul`, `Div`, `Inc` | Core integer ALU operations; frontends usually emit these from operators. |
| `Maths` | `Sin`, `Cos`, `Tan`, `Exp`, `Log`, `Log10`, `Power`, `Sqrt`, `Clamp`, `Lerp`, `Random`, `RandomRange` | Deterministic integer/fixed-point helpers. Trig/log/exp use Q16.16 fixed point. RNG-class methods require random capability. |
| `Bits` | `And`, `Or`, `Xor`, `Not`, `Shl`, `Shr`, `Sar` | Bitwise and shift operations. |
| `Dot8` | `Len`, `Of` | Signed int8 dot product; native C can use AArch64 SDOT or Cortex-M33 SMLAD. |

Example:

```c
int mask = Bits.And(flags, MASK16);
int root = Maths.Sqrt(144);
int s = Maths.Sin(RAD_PER_DEG_Q16 * 90);
```

## Byte arrays and spans

| Namespace | Methods | Notes |
|-----------|---------|-------|
| `Memory` | `Set`, `Get`, `ArenaInit`, `ArenaAlloc`, `ArenaReset`, `ArenaStats`, `Peek`, `Poke` | Byte-addressable arena and lower-level memory hooks. Prefer string literals/spans unless byte-by-byte work is the point. |
| `Span` | `Make`, `Slice`, `Materialize`, `Len`, `Get` | A `span` is the byte-array handle. `Slice` is zero-copy; `Materialize` copies into a new contiguous span. |
| `Io` | `Write`, `WriteByte` | Output bytes/spans. |

Example:

```c
int data = "hello";          // string literal -> UTF-8 span
print(Span.Len(data));       // 5
Io.Write(Span.Slice(data, 1));
```

## Strings

`String.*` methods operate on UTF-8 byte spans and return either integers or new
spans.

| Method | Meaning |
|--------|---------|
| `Length` | byte length |
| `Concat` | concatenate two spans |
| `Substring` | slice from an offset |
| `IndexOf` | find a subspan, sets `Status.Last` to `NOT_FOUND` on miss |
| `StartsWith`, `EndsWith`, `Eq` | comparisons |
| `ToUpper`, `ToLower`, `Trim` | ASCII-oriented text transforms |
| `SetReplace`, `Replace` | set replacement span, then replace a needle |
| `Split`, `Join` | ABI surface for split/join workflows |

Example:

```c
int name = "Ada";
int upper = String.ToUpper(name);
Io.Write(upper);             // ADA
```

## Number formatting and parsing

| Method | Meaning |
|--------|---------|
| `Parse` | parse decimal text span to int; sets `Status.Last` on parse error |
| `ToString`, `ToHex`, `ToOctal`, `ToBinary` | integer to text span |
| `Abs`, `Min`, `Max`, `Floor`, `Ceiling`, `Round` | integer numeric helpers |

Example:

```c
int n = Number.Parse("42");
Io.Write(Number.ToHex(n));    // 2a
```

## Text/binary encodings

| Namespace | Methods | Notes |
|-----------|---------|-------|
| `Base64` | `Encode`, `Decode`, `UrlEncode`, `UrlDecode` | Standard and URL-safe Base64. |
| `Encoding` | `AsciiEncode`, `AsciiDecode`, `Utf8Encode`, `Utf8Decode`, `Utf16LEEncode`, `Utf16LEDecode`, `Utf16BEEncode`, `Utf16BEDecode`, `Utf7Encode`, `Utf7Decode`, `HexEncode`, `HexDecode` | Explicit text/binary conversion. Decoders normalize text back to UTF-8 spans. |

Example:

```c
int text = "Hello+£";
int utf16 = Encoding.Utf16LEEncode(text);
int back = Encoding.Utf16LEDecode(utf16);
int b64 = Base64.UrlEncode(text);
int raw = Base64.UrlDecode(b64);
int hex = Encoding.HexEncode(raw);
```

`Utf7*` exists for compatibility only. Prefer UTF-8/UTF-16 for new protocols.

## Streaming builders

| Namespace | Methods | Notes |
|-----------|---------|-------|
| `Utf8Writer` | `New`, `Byte`, `Int`, `Span`, `ToSpan`, `Len`, `Reset` | Build byte/text output in a caller-provided arena window. |
| `Utf8Reader` | `New`, `Peek`, `Next`, `Int`, `SkipWs`, `Eof`, `Pos`, `Match` | Scan bytes from a span. |
| `Json` | `BeginObject`, `EndObject`, `BeginArray`, `EndArray`, `Key`, `Str`, `Int`, `Bool`, `Null`, `Raw` | Streaming JSON with commas and escaping handled by runtime. |
| `Xml` | `Open`, `AttrName`, `AttrValue`, `OpenEnd`, `Text`, `Close`, `Empty` | Escaped XML/HTML-style element writer. |
| `TextRender` | `Raw`, `Text`, `Open`, `Attr`, `OpenEnd`, `Close`, `Empty`, `Hole`, `Br` | HTML streaming over `Utf8Writer`; escapes text/attributes and renders simple model holes. |

Example:

```c
int w = Utf8Writer.New(3000, 512);
Json.BeginObject(w);
Json.Key(w, "status");
Json.Str(w, "ok");
Json.EndObject(w);
Io.Write(Utf8Writer.ToSpan(w));
```

## Hashing, crypto, and RNG

| Namespace | Methods | Notes |
|-----------|---------|-------|
| `Random` | `U32` | Host-injected deterministic/random U32 source; capability-gated. |
| `Crypto` | `Sha256`, `HmacSha256`, `Encrypt`, `Decrypt`, `RandomBytes` | Implemented runtime crypto primitives; encrypt/decrypt and random bytes are capability-gated. |
| `Crypto` ABI surface | `Sha1`, `Sha512`, `Md5`, `Blake2b`, `Blake3`, `HmacSha512`, `GenerateKeyPair`, `Sign`, `Verify`, `DeriveKey` | Hook surface for host-backed crypto; only use when the target host advertises support. |

Example:

```c
int digest = Crypto.Sha256("payload");
int sig = Crypto.HmacSha256("key", "payload");
int nonce = Random.U32();
```

## Compression

| Namespace | Methods |
|-----------|---------|
| `Compress` | `PicoCompress`, `PicoDecompress`, `BrotliCompress`, `BrotliDecompress`, `DeflateCompress`, `DeflateDecompress`, `GzipCompress`, `GzipDecompress` |

Example:

```c
int packed = Compress.GzipCompress("hello");
int plain = Compress.GzipDecompress(packed);
Io.Write(plain);
```

## Capability notes

Pure byte/text/math transforms need no external binding. Hooks that touch outside
state are capability-gated: `Random.*`, `Crypto.RandomBytes`, `Crypto.Encrypt`,
`Crypto.Decrypt`, `Storage.*`, `Req.*`, `Resp.*`, `DateTime.*`, `Locale.*`,
device/stream/process/timer hooks, and similar host bindings.

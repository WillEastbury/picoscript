# Compression — codecs built into the runtime

`Compress.*` provides compression built into the runtime — no host `zlib`. The
recommended in-runtime codec is **PicoCompress** (the real, vendored
[`picocompress`](https://github.com/WillEastbury/picocompress) library — byte-identical
on all 5 runtimes); DEFLATE/gzip are kept for outside-world **interop**; Brotli is
host-supplied.

| Hook | Code | Effect |
|------|------|--------|
| `Compress.PicoCompress(span)`   | `0x0102` | **real deterministic LZ77** — byte-identical on every runtime |
| `Compress.PicoDecompress(span)` | `0x0103` | inverse of PicoCompress |
| `Compress.DeflateCompress(span)`   | `0x0106` | raw DEFLATE (RFC 1951) — for zlib interop |
| `Compress.DeflateDecompress(span)` | `0x0107` | raw INFLATE |
| `Compress.GzipCompress(span)`      | `0x0104` | gzip (RFC 1952): header + deflate + CRC-32 + ISIZE |
| `Compress.GzipDecompress(span)`    | `0x0105` | parse gzip header + inflate |
| `Compress.Brotli*` | `0x0100/0x0101` | **host-supplied** (PIOS libbrotli); unimplemented in-runtime |

Malformed/truncated input never hangs the VM (the inflater raises; PicoDecompress
guards bad back-distances).

## PicoCompress — the byte-identical compressor (preferred)

PicoCompress is the real **`picocompress` library** ([`WillEastbury/picocompress`](https://github.com/WillEastbury/picocompress)),
vendored into this repo so the runtime carries no host dependency. The three ports
used by the VMs are byte-for-byte the library's own ports:

| Runtime | Vendored file | Upstream |
|---------|---------------|----------|
| Python VM      | `picocompress.py`     | `ports/python/picocompress.py` |
| JS VM          | `vm/picocompress.js`  | `ports/javascript/picocompress.mjs` (UMD-wrapped) |
| C VM (hosted)  | `vm/picocompress.c` / `.h` | `src/picocompress.c` |

The codec is a real LZ with a **block layout** (508-byte payload blocks, a 4-byte
header per block) over these tokens:

```
0x00..0x3F  literal run, (tag+1) bytes follow
0x40..0x7F  static-dictionary word, index 0..63
0x80..0xBF  short LZ match: 5-bit length + 9-bit back-distance
0xC0..0xCF  repeat-offset match (reuse a recent distance)
0xD0..0xDF  static-dictionary word, index 80..95
0xE0..0xEF  static-dictionary word, index 64..79
0xF0..0xFF  long LZ match: length + 2-byte back-distance
```

The encoder pipeline is **repeat-offset cache → 96-word static dictionary → LZ
hash-chain finder** (hash3 `*251 + *11 + *3`, chain depth 2, one lazy step, 9 hash
bits). Because the algorithm is fully specified, the compressed *bytes* are identical
on the Python, JS and C VMs **by construction** — verified in
`tests/test_picocompress.py` (Python VM == JS VM == C VM == the library, plus
round-trip on each). It runs byte-identically on all **five** paths (Python/JS/C VMs
+ the two transpilers); see also `examples/hashing.pc` in `tests/test_examples_parity.py`.

> **Hosted vs freestanding.** `vm/picocompress.c` needs `<string.h>`, so it is compiled
> into the C VM only on hosted targets (`#if __STDC_HOSTED__`). On freestanding/embedded
> builds (the Cortex-M33 / AArch64 cross-compiles, PIOS) `Compress.PicoCompress` falls
> through to the host-fillable hook and is supplied by the platform.
>
> **Upstream note.** While vendoring we found a transposed byte in the library's Python
> port: `STATIC_DICT[13]` was `b'","'` but the canonical C/JS reference is `b',",'`
> (`[0x2c,0x22,0x2c]`). The vendored copy here is fixed; the upstream
> `picocompress/ports/python/picocompress.py` should be synced.

## DEFLATE / gzip — for interop

Real DEFLATE (RFC 1951) + gzip (RFC 1952), kept for interoperating with the outside
world (stdlib `zlib`/`gzip` read our output; we decompress theirs). The compressor is
canonical (one fixed-Huffman block, greedy LZ77, deterministic hash-chain finder) so
its bytes are identical on the Python and JS VMs. **Decompression runs on all 3 VMs**
(Python/JS + native C `inflate`/`gunzip`, puff.c-adapted), verified against real
stdlib gzip with dynamic-Huffman blocks. The exact-3-byte-prefix DEFLATE *compressor*
is Python/JS only (a byte-identical C deflate compressor would need a 64 MiB table or
a bounded-hash rework — and PicoCompress is already byte-identical on C), so prefer
PicoCompress in-runtime and gzip for interop.

## Brotli

A real, byte-identical hand-written Brotli (RFC 7932: a 122 KiB static dictionary,
context modelling and complex prefix codes) is not feasible across three VMs.
`Compress.Brotli*` is therefore **host-supplied** — PIOS binds a real libbrotli — and
remains an unimplemented host-fillable stub in the runtime.

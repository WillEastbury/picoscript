# Compression — codecs built into the runtime

`Compress.*` provides compression built into the runtime — no host `zlib`. The
recommended in-runtime codec is **PicoCompress** (a real, byte-identical LZ77 on all
5 runtimes); DEFLATE/gzip are kept for outside-world **interop**; Brotli is
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

PicoCompress is a real LZ77, **byte-oriented** (no bit packing) with a **head-only**
bounded-hash match finder (4096 buckets, 64 KiB window) — so the compressed *bytes*
are identical on the Python, JS and C VMs **by construction**, with no 64 MiB table
(embedded-friendly: just a 4096-entry hash). Token stream:

```
tag 0x00..0x7F  -> literal run of (tag+1) bytes, then those bytes
tag 0x80..0xFF  -> match: length (tag-0x80)+3, then a 2-byte LE back-distance
```

It is the recommended in-runtime codec and runs byte-identically on all **five**
paths (Python/JS/C VMs + the two transpilers) — see `examples/hashing.pc` in
`tests/test_examples_parity.py`, and the cross-VM byte-identity check in
`tests/test_compress.py::test_picolz_compressed_bytes_py_equals_js_equals_c`.

## DEFLATE / gzip — for interop

Real DEFLATE (RFC 1951) + gzip (RFC 1952), kept for interoperating with the outside
world (stdlib `zlib`/`gzip` read our output; we decompress theirs). The compressor is
canonical (one fixed-Huffman block, greedy LZ77, deterministic hash-chain finder) so
its bytes are identical on the Python and JS VMs. **Decompression runs on all 3 VMs**
(Python/JS + native C `inflate`/`gunzip`, puff.c-adapted), verified against real
stdlib gzip with dynamic-Huffman blocks. The exact-3-byte-prefix DEFLATE *compressor*
is Python/JS only (a byte-identical C deflate compressor would need a 64 MiB table or
a bounded-hash rework — and PicoCompress already covers the byte-identical-everywhere
need), so prefer PicoCompress in-runtime and gzip for interop.

## Brotli

A real, byte-identical hand-written Brotli (RFC 7932: a 122 KiB static dictionary,
context modelling and complex prefix codes) is not feasible across three VMs.
`Compress.Brotli*` is therefore **host-supplied** — PIOS binds a real libbrotli — and
remains an unimplemented host-fillable stub in the runtime.

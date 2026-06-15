# Compression — real DEFLATE / gzip in the runtime

`Compress.*` provides real, standards-compliant compression built into the runtime —
no host `zlib`. It interoperates both ways with the outside world (stdlib `zlib`/`gzip`
read our output; we decompress theirs).

| Hook | Code | Effect |
|------|------|--------|
| `Compress.DeflateCompress(span)`   | `0x0106` | raw DEFLATE (RFC 1951) |
| `Compress.DeflateDecompress(span)` | `0x0107` | raw INFLATE |
| `Compress.GzipCompress(span)`      | `0x0104` | gzip (RFC 1952): header + deflate + CRC-32 + ISIZE |
| `Compress.GzipDecompress(span)`    | `0x0105` | parse gzip header (incl. FEXTRA/FNAME/FCOMMENT/FHCRC) + inflate |
| `Compress.PicoCompress` / `PicoDecompress` | `0x0102/0x0103` | the simple byte-run RLE (unchanged) |
| `Compress.Brotli*` | `0x0100/0x0101` | **unimplemented** host-fillable stub |

Malformed/truncated input never hangs the VM: the inflater raises and the hook returns
an empty span with `host_status = 2`.

## Canonical, byte-identical strategy

To make compressed bytes **byte-identical across VMs**, the compressor uses one fixed
strategy: a single final **fixed-Huffman block** with **greedy LZ77** over a 32 KiB
window and a deterministic hash-chain match finder (chains keyed by the exact 3-byte
prefix; up to 256 probes). Inflate is spec-complete (stored + fixed + dynamic Huffman),
so it reads any valid DEFLATE/gzip stream. Typical ratios ~0.03–0.46.

Implemented in `picoscript_vm.py` (`_deflate`/`_inflate`/`_crc32`/`_gzip_*`) and mirrored
byte-for-byte in `vm/picovm.js`. `tests/test_compress.py` checks round-trip, **two-way
interop** with stdlib `zlib`/`gzip`, and `Python VM == JS VM` (including the compressed
bytes).

## Decision record: the C VM

The Python and JS VMs (the reference runtime + the browser) ship the full codec.
The **native C VM (`vm/picovm.c`) does not yet implement compress/decompress**, a
deliberate tradeoff:

- **Decompression output is canonical** — any spec-correct inflater produces identical
  bytes — so a C `inflate`/`gunzip` is straightforward to add later with no
  byte-identity risk. *(Recommended next step for on-device asset/firmware decompression.)*
- **Compression bytes are not canonical** — byte-identity requires the *same* match
  finder. Matching the Python/JS exact-3-byte-prefix chains needs either a 64 MiB
  direct-mapped table (embedded-hostile) or a shared bounded-hash rework across all
  three VMs. Rather than ship a 64 MiB array or a subtly-divergent compressor, the
  canonical compressor stays in the reference runtime + the PIOS host.

Net: programs run real gzip/deflate on the Python and JS VMs today; on the native path
the codec is host/PIOS-supplied (or a future canonical C port). No 5-path example uses
`Compress.Deflate*`/`Gzip*`, so this asymmetry doesn't affect the parity suite. If you
want the C port (inflate first, or a bounded-hash compressor across all three VMs), say so.

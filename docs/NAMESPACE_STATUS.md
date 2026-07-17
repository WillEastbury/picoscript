# Namespace status — what's implemented, and the hard reasons for the rest

> **See `docs/FEATURE_MATRIX.md` for the current, complete per-namespace x
> per-runtime status table** (all 70 namespaces, Python/JS/C VM parity,
> verified by direct dispatch-code inspection). This document predates that
> full audit and covers only the namespaces implemented in one earlier
> session; kept for historical context on *why* each gap existed at the time.

This session moved most "planned" namespaces to implemented. For the remainder,
each entry below gives the **actual reason** it isn't a self-contained VM
primitive — not an excuse. The recurring theme: the PicoScript VM is
**deterministic** and has **no host environment**, so anything that depends on
external state (clock, OS, entropy, network, keystore) cannot be a pure,
parity-testable primitive — it must be **injected by the host/PIOS kernel**
(the VM already does this for `Req.*`). Everything else is scope/effort, and
where that was my only objection I implemented a representative op to prove it.

## Implemented this session
`Bits.*`, `Dot8.*` (NEON SDOT / SMLAD), native `Memory.*`/`Io`, `String.*`,
`Number.*`, `Maths.Power/Sqrt`, `Template.*` (holes/sections/`{{#each}}`),
`Compress.PicoCompress/PicoDecompress`, `Compress.BrotliCompress/BrotliDecompress`,
`Compress.Gzip*`/`Deflate*`, `Crypto.Sha256` + `Crypto.HmacSha256`
(RFC 2104, parity-tested to RFC 4231 vectors), `Html.Encode/Decode`,
`Http.ParseQuery/ParseForm` (url-decode -> Template model) + `Http.EncodeJson/ParseJson`
(model <-> JSON, nested JSON flattens to the `{{#each}}` model).

> **Full five-path parity.** Every namespace above is native not just in the three
> bytecode interpreters (Python/JS/C) but in **both transpilers** too: `lower_to_c`
> (-> native C) and `lower_to_js` (-> native JS) lower each host op to a first-class
> code-keyed call (`pv_host2` / `rt.host(code,…)`), skipping the VM. One host
> implementation per language, zero divergence; checked by `tests/test_native_toc.py`
> (4 runtimes from one source) and `tests/test_examples_parity.py` (`examples/*.pc`).
> `Utf8Writer`/`Utf8Reader`/`Json`/`Xml` are now native in the C runtime too
> (`tests/test_textio.py`), so **every pure namespace runs on all five paths**. The
> only non-portable namespaces are the host-injected ones below (`Req`/`Resp`/
> `Storage` read live host resources; clock/OS/entropy are external by design).

(Already present: `Io`, `Json`, `Xml`, `Queue`, `Random`, `Req`, `Resp`, `Span`,
`Storage`, `Utf8Reader`, `Utf8Writer`.)

> **Native HTTP server (C runtime).** `Req.*` (`Method/Path/Header/BodySpan/
> BodyLen/Param/ParamCount/Principal`) and `Resp.*` (`Status/Header/Write/End`)
> are now implemented natively in `vm/picovm.c`, fed by the thread-pooled
> `vm/picovm_pool.c` HTTP runtime — so a PicoScript program compiles via
> `lower_to_c` to a standalone native HTTP server. Storage stays host-injected
> via the `pv_storage_hook` extension point. `Req.Principal()` reads the trusted
> `X-Forge-Principal` header (authentication stays in the host/kernel/proxy).
> See `docs/NATIVE_HTTP_SERVER.md`.

> **Correction:** I previously claimed codes >0xFF "can't be dispatched (8-bit
> aliasing)". **That was wrong.** The lowerer emits `EXT_HOST_HOOK_BASE (0x6000)
> | (code & 0xFFF)` for codes >0xFF and the VM decodes `imm16 & 0xFFF`, so
> `Compress`/`Crypto`/`Html`/`Http`/`X509`/`Auth`, `Storage` slices
> (`0x1A0+`), and request/stream/event slices (`0x176+`, `0x1B0+`) dispatch fine —
> proven by `Crypto.Sha256("abc")` matching the known digest.

## Hard reasons — genuinely cannot be a self-contained deterministic primitive

**External nondeterministic state (host-injected by design).** The VM has no
clock, no OS, no entropy. These return values that are *not functions of their
inputs*, so they can't be computed in-VM or parity-tested to a fixed value — the
host/PIOS kernel must supply them (as it does for the request context):
- `DateTime.Now` / `UtcNow` (and "now" timestamps) — wall clock.
- `Environment.*` (OS version, CPU count, memory, hostname, timezone, pids,
  elapsed time) — host/OS facts.
- `Maths.Random` / `RandomRange`, `Crypto.RandomBytes` — entropy. (`Random.U32`
  is deliberately seeded from clock + a startup offset, i.e. non-deterministic.)
- `Context.*` (user, remote addr, client cert, headers…) and `Locale` state —
  the live request/connection and host locale.

**64-bit-word algorithms in JS.** `Crypto.Sha512` / `Blake2b` / `Blake3` operate
on 64-bit words. JavaScript has no native 64-bit integer (Numbers are float64;
bitwise ops are 32-bit), so a *browser-safe pure-JS* impl needs BigInt or hi/lo
emulation — feasible but slower and a real divergence risk. `Sha256` (32-bit) is
implemented; the 64-bit ones are deferred for this reason.

## Scope/effort (not impossible — explicitly deferred, with the doable proof shipped)

- **Float transcendentals** — `Maths.Sin/Cos/Tan/Log/Log10/Exp/Lerp`. PicoScript
  is integer-only, so these need a chosen fixed-point format (e.g. Q16.16) +
  polynomial/CORDIC. A design choice + work, not a blocker. (`Power`/`Sqrt`,
  which are integer-exact, are implemented.)
- **3-argument ops** — `Maths.Clamp(x,lo,hi)`, `Lerp(a,b,t)`. The host-hook ABI
  is 2-in/1-out, so a 3-arg op needs the stateful 2-call pattern (like
  `String.SetReplace` / `Dot8.Len`) or an `imm16`-carried constant. Doable.
- **Standard codecs** — implemented. `PicoCompress` is the vendored
  `picocompress` codec. `Brotli` is a real in-runtime RFC 7932 encoder/decoder
  whose output browser/zlib decoders accept. `Gzip`/`Deflate` are kept for
  outside-world interop. All are parity-tested across the runtime paths that
  implement them; see `docs/COMPRESS.md`.
- **HTML DOM (now implemented) + HTTP parsing** — `Html.CreateNode/
  AddChildNode/RemoveChildNode/SetAttribute/GetAttribute/ParseTree/Serialize/
  QuerySelector` are now a real, pure, deterministic mutable node table + a
  minimal permissive HTML parser (no host state needed) — see
  `docs/FEATURE_MATRIX.md`'s "Html.* DOM tree ops — from stub to real"
  section and `tests/test_html_dom.py`; verified byte-identical on all five
  execution paths. The pure HTTP parsers **are implemented**:
  `Http.ParseQuery/ParseForm` (URL-decode -> `key=value` Template model),
  `Http.EncodeJson` (model -> JSON object with escaping) and
  `Http.ParseJson` (JSON -> dotted-path model, so nested JSON feeds
  `{{#each}}`). Only `Http.ReadHeader/ReadBody/GenerateHeaders/
  GenerateResponse` remain — they read/write the host connection (host-
  injected). `Html.Encode/Decode` (pure) are implemented.
- **Asymmetric / symmetric crypto** — `Crypto.Sign/Verify/Encrypt/Decrypt/
  GenerateKeyPair/DeriveKey`, `X509.*`, `Auth.*`. Large security-sensitive
  primitives (RSA/EC/AES) + key management; signing/keygen also need entropy
  (external), and X509/Auth need a host trust store / identity provider /
  network. The pure, doable slices are implemented: `Sha256` and now
  `HmacSha256` (RFC 2104 over the canonical SHA-256 — two input spans key+message,
  32-byte digest span; byte-identical on all five paths, parity-tested to the
  RFC 4231 vectors incl. the >64-byte key-hashed-first case). `HmacSha512`/`Sha512`
  remain deferred for the 64-bit-word-in-JS reason above.

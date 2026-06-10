# Namespace status — what's implemented, and the hard reasons for the rest

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
`Compress.PicoCompress/PicoDecompress` (RLE), `Crypto.Sha256`, `Html.Encode/Decode`,
`Http.ParseQuery/ParseForm` (url-decode -> Template model) + `Http.EncodeJson/ParseJson`
(model <-> JSON, nested JSON flattens to the `{{#each}}` model).
(Already present: `Io`, `Json`, `Xml`, `Queue`, `Random`, `Req`, `Resp`, `Span`,
`Storage`, `Utf8Reader`, `Utf8Writer`.)

> **Correction:** I previously claimed codes >0xFF "can't be dispatched (8-bit
> aliasing)". **That was wrong.** The lowerer emits `EXT_HOST_HOOK_BASE (0x6000)
> | (code & 0xFFF)` for codes >0xFF and the VM decodes `imm16 & 0xFFF`, so
> `Compress`/`Crypto`/`Html`/`Http`/`X509`/`Auth` (0x100–0x149) dispatch fine —
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
- **Standard codecs** — `Compress.Brotli/Gzip/Deflate`. Require full spec-exact
  LZ77+Huffman bitstream codecs, bit-identical across Python and JS. The custom
  `PicoCompress` (RLE) is implemented and covers the embedded case.
- **HTML DOM + HTTP parsing** — `Html.CreateNode/SetAttribute/QuerySelector/
  ParseTree/Serialize` need a mutable tree model + parser. The pure HTTP parsers
  **are implemented**: `Http.ParseQuery/ParseForm` (URL-decode -> `key=value`
  Template model), `Http.EncodeJson` (model -> JSON object with escaping) and
  `Http.ParseJson` (JSON -> dotted-path model, so nested JSON feeds `{{#each}}`).
  Only `Http.ReadHeader/ReadBody/GenerateHeaders/GenerateResponse` remain — they
  read/write the host connection (host-injected). `Html.Encode/Decode` (pure) are
  implemented.
- **Asymmetric / symmetric crypto** — `Crypto.Sign/Verify/Encrypt/Decrypt/
  GenerateKeyPair/DeriveKey`, `X509.*`, `Auth.*`. Large security-sensitive
  primitives (RSA/EC/AES) + key management; signing/keygen also need entropy
  (external), and X509/Auth need a host trust store / identity provider /
  network. Hashing (`Sha256`) is the pure, doable slice and is implemented.

# PicoScript examples

Small programs in the various PicoScript frontends. Every program runs **byte-for-byte
identically** on all five PicoScript runtimes — the three bytecode interpreters
(Python `picoscript_vm.py`, JS `vm/picovm.js`, C `vm/picovm.c`) and the two
transpilers that **skip the VM entirely** (`lower_to_c` → native C, `lower_to_js` →
native JS).

## Namespace demos (the host library, first-class on all five runtimes)

| File | Shows |
|------|-------|
| `text_tools.pc` | `String.ToUpper/Length`, `Number.Parse/ToHex`, `Html.Encode` over arena spans |
| `web_template.pc` | the picoweb flow: `Http.ParseQuery` → key=value model → `Template.Compile`/`Render` |
| `hashing.pc` | `Crypto.Sha256` (32-byte digest) + the real `Compress.PicoCompress`/`PicoDecompress` codec |

These are exercised by `tests/test_examples_parity.py`, which runs each on all five
runtimes and asserts identical output.

## Testing

Write tests **in PicoScript** with the `Assert.*` namespace and run them with the
PSUnit harness (`python psunit.py [--parity]`). Tests live in `tests/psunit/` (one per
frontend) and can seed the `Storage.*`/`Gpio.*`/`Stream.*` provider seams before
asserting. See [`docs/PSUNIT.md`](../docs/PSUNIT.md).

## Language / compute demos

| File | Frontend | Shows |
|------|----------|-------|
| `hello.pico` | v1 | minimal program |
| `sum.pc` | C-syntax | `for` loop, `Storage`, `Net.*` response |
| `sum.ppy` / `sum.eng` | Python-like / English-like | the same sum in other frontends |
| `fizzbuzz.pbas` | BASIC-like | control flow |
| `constants_enums.pc` / `.pbas` / `.ppy` / `.eng` | all four | built-in named constants plus user-defined `const` / `enum` declarations |
| `locale_formatting.pc` | C-syntax | `Locale.SetLocale`, `FormatDate`, `FormatTime`, `FormatNumber`, `FormatCurrency` |
| `encoding_roundtrip.pc` | C-syntax | `Encoding.*` ASCII/UTF-16/hex round-trips + `Base64.UrlEncode/UrlDecode` |
| `model_block_slice.pc` | C-syntax | `Model.SetBlock`, `ReadTensorBlock`, `MatVecI8Block` over card-backed tensors |
| `filter.pico` | v1 | branching |
| `selfhost_emit.pc` / `selfhost_asm.pc` | C-syntax | PicoScript emitting runnable PicoScript |
| `bitnet_ternary_matvec.pc` / `bitnet_k_matvec.pc` / `bitnet_int8_matvec.pc` | C-syntax | quantized BitNet kernels (`Dot8` → NEON SDOT / Cortex-M33 SMLAD) |

## Running a program on each path

```bash
# 1) Python interpreter
python -c "from picoscript_cfront import compile_c; from picoscript_il import lower_to_bytecode_safe; \
           from picoscript_vm import PicoVM; \
           w=lower_to_bytecode_safe(compile_c(open('examples/web_template.pc').read())); \
           print(bytes(b for o in PicoVM().run(w).output for b in o))"

# 2) C interpreter (bytecode)
python -m ziglang cc -std=c99 -O2 vm/picovm.c vm/picovm_run.c -o vm/picovm_run.exe
#   ...then feed it "<count>\n<hex words>" on stdin (see tests/test_examples_parity.py)

# 3) Native C (transpile, skip the VM)
python -c "from picoscript_cfront import compile_c; from picoscript_il import lower_to_c; \
           print(lower_to_c(compile_c(open('examples/web_template.pc').read()), emit_main=True))" > out.c
python -m ziglang cc -std=c99 -O3 -Ivm out.c vm/picovm.c -o out.exe && ./out.exe

# 4) Native JS (transpile, skip the VM)
python -c "from picoscript_cfront import compile_c; from picoscript_il import lower_to_js; \
           print(lower_to_js(compile_c(open('examples/web_template.pc').read())))" > out.js
#   require('./out.js').run() — its runtime delegates host calls to vm/picovm.js
```

For native cross-compilation to a target use `picoscript_build.py native --profile {host,pi5,pico2}`.

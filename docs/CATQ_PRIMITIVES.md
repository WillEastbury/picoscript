# CAT-Q accelerator primitives

PicoScript exposes CAT-Q as coarse host-backed superinstructions. The executable
pipeline is written in the C-syntax PicoScript dialect; CUDA, QPU, NEON, CPU, or
another host backend supplies the execution substrate beneath those operations.

No new core bytecodes are used. Every operation is an extended host hook, so the
same PicoScript source lowers through bytecode, native C, and native JavaScript.

## Tensor operations

| Method | Inputs | Result |
|---|---|---|
| `Tensor.Map(source, options)` | source span, options span | opaque tensor handle |
| `Tensor.View(tensor, spec)` | tensor handle, view-spec span | tensor handle |
| `Tensor.Gemm(lhs, rhs)` | tensor handles | tensor handle |
| `Tensor.Reduce(tensor, spec)` | tensor handle, reduction spec | tensor/scalar handle |
| `Tensor.Elementwise(tensor, spec)` | tensor handle, operation spec | tensor handle |

The host defines option formats. Text spans such as `dtype=bf16;device=cuda:0`
are recommended for human-authored programs; a host may also accept a binary
descriptor.

## CAT-Q operations

| Method | Inputs | Result |
|---|---|---|
| `CatQ.Calibrate(calibration, options)` | calibration tensor, CAT-Q options span | CAT-Q context |
| `CatQ.Optimize(context, weights)` | context, weight tensor | optimized tensor/state |
| `CatQ.Ternarize(context, optimized)` | context, optimized tensor/state | ternary tensor |
| `CatQ.Pack(context, ternary)` | context, ternary tensor | packed tensor or span |

`CatQ.Optimize` is deliberately coarse. The dependency-free native provider in
`vm/picovm_catq.c` implements the paper's analytical gradients and AdamW update
directly in C. Accelerated providers can replace that hook without changing the
PicoScript workflow.

## Jobs and shards

| Method | Inputs | Result |
|---|---|---|
| `Async.Submit(request, resource)` | request span, resource handle | job handle |
| `Async.Wait(job, timeoutMs)` | job handle, timeout | completion status |
| `Async.Result(job)` | job handle | result handle/span |
| `Shard.Load(path, options)` | path span, options span | shard handle |
| `Shard.Save(shard, path)` | shard handle, path span | success |

## Span transport

The existing raw socket surface now has client and span-oriented operations:

| Method | Inputs | Result |
|---|---|---|
| `Net.Connect(endpoint, port)` | endpoint span, port | connection handle |
| `Net.SendSpan(connection, span)` | connection, payload span | bytes sent |
| `Net.RecvSpan(connection, maxBytes)` | connection, maximum bytes | received span |

`Net.Listen`, `Net.Accept`, `Net.Read`, `Net.Write`, and `Net.Shutdown` use the
same host provider. `vm/picovm_net.c` supplies a synchronous native socket
provider; `SocketNetworkProvider` supplies the Python reference provider.
JavaScript hosts inject `networkProvider` because browser and Node transports
have different event-loop requirements.

## Host bindings

Python:

```python
host = HostApi(compute_provider=accelerator, network_provider=network)
PicoVM(host=host).run(words)
```

JavaScript:

```javascript
const vm = new PicoVM({ computeProvider, networkProvider });
```

Emitted JavaScript accepts the same options:

```javascript
const rt = program.makeRuntime({ computeProvider, networkProvider });
program.run(rt);
```

Native C:

```c
pv_catq_install();
pv_net_install_socket_provider();
```

Build the executable C-PicoScript workflow with:

```powershell
python picoscript_build.py native examples\catq_quantize.pc --provider catq
```

This links `picovm.c` and `picovm_catq.c`; no Python ML framework or external
tensor library is used by the resulting executable.

## C-PicoScript workflow

```c
int shard = Shard.Load("model.safetensors", "mmap");
int calibrationShard = Shard.Load("calibration.safetensors", "mmap");
int weights = Tensor.Map(shard, "tensor=model.layers.0.mlp.down_proj.weight");
int calibration = Tensor.Map(calibrationShard, "tensor=calibration");
int catq = CatQ.Calibrate(calibration, "group=128;epochs=60;batch=3");
int optimized = CatQ.Optimize(catq, weights);
int ternary = CatQ.Ternarize(catq, optimized);
int packed = CatQ.Pack(catq, ternary);
Shard.Save(packed, "model.ternary.safetensors");
```

The complete executable flow is `examples/catq_quantize.pc`.

### Ternary inference

CAT-Q weights cannot use the older unscaled `BitLinear.MatVecTernary` directly:
CAT-Q stores a floating scaling factor for every flattened weight group.
`BitLinear.MatVecCatQ(packed, activation)` consumes both the packed two-bit
weights and their group scales.

Run the real Qwen layer demonstration:

```powershell
pwsh -File tools\run_catq_ternary_infer.ps1
```

It loads the CAT-Q shard produced by `run_catq_smoke.ps1`, selects one 1024-value
activation vector, executes the 3072x1024 gate projection through the PicoScript
ternary stack, saves the result, and checks that all 3072 outputs are finite.

This is a genuine CAT-Q ternary layer inference path. It is not yet a complete
autoregressive Qwen model: embeddings, all transformer layers, attention/KV
state, nonlinearities, final norm, LM head, sampling, and tokenizer orchestration
still need to be assembled around this now-correct projection primitive.

### Qwen ternary MLP block

The next assembled graph is a complete Qwen SwiGLU MLP block:

```powershell
pwsh -File tools\run_qwen_ternary_mlp.ps1
```

The C-PicoScript graph performs:

```text
RMSNorm(input)
  -> CAT-Q gate projection
  -> CAT-Q up projection
  -> Tensor.SwiGLU(gate, up)
  -> CAT-Q down projection
  -> residual add
```

Layer norm weights remain BF16, while all three matrix projections use saved
CAT-Q ternary shards and their learned group scales.

### Host SIMD

Hosted CAT-Q runners compile with `--profile host`, enabling AVX2/FMA on capable
x86 machines while retaining scalar fallbacks for other targets. The native
provider has explicit vector kernels for:

- group mean and absolute-deviation statistics;
- simultaneous reference/quantized dot products;
- gradient AXPY updates;
- RMSNorm sum-of-squares;
- scaled packed CAT-Q ternary matrix-vector products.

Measured on an Intel i9-12900H with the 3.15M-weight Qwen gate projection:

| Operation | Previous | AVX2/hoisted group state |
|---|---:|---:|
| Two-epoch CAT-Q smoke conversion | ~4.7s | **1.6s** |
| Saved CAT-Q 3072x1024 projection | 87ms | **71ms** |
| Complete CAT-Q Qwen MLP | 128ms | **81ms** |

The packed ternary codes are byte-identical to the previous implementation.
Floating scales differ only through reduction order, with observed maximum
absolute difference below `4.3e-8`.

### Reproducible smoke test

On Windows, the complete small-model test is scripted:

```powershell
pwsh -File tools\run_catq_smoke.ps1
```

By default it downloads `Qwen/Qwen3-0.6B-Base` when absent, reads the real
`model.layers.0.mlp.gate_proj.weight` BF16 tensor, creates deterministic
synthetic calibration activations, generates C-PicoScript with `catq_plan`,
builds it with the native CAT-Q provider, executes it, and validates:

- safetensors metadata, source shape, and group size;
- packed codes contain only `-1`, `0`, and `+1`;
- all group scales are finite and positive;
- packed value count matches the source tensor.

Use `-Epochs 60 -CalibrationRows 512 -BatchSize 3` only with meaningful captured
activations; the defaults are intentionally a fast wiring test.

### LLM output through PicoScript networking

`examples/llm_paris_client.pc` is a compiled C-PicoScript HTTP client using
`Net.Connect`, `Net.SendSpan`, and `Net.RecvSpan`. With the official
Qwen3-0.6B instruct checkpoint available locally, run:

```powershell
pwsh -File tools\run_llm_paris_demo.ps1
```

The runner starts a local OpenAI-compatible model server when needed, builds the
PicoScript client with the native socket provider, decodes the exact bytes emitted
by PicoScript, and checks that the answer identifies Paris and the Eiffel Tower.

## Model-wide plan generation

CAT-Q requires the activation tensor that actually enters each quantized weight;
reusing one calibration matrix for every layer or MoE expert is incorrect.
`tools/catq_plan.c` therefore compiles an explicit weight/activation manifest
into C-syntax PicoScript.

Build the dependency-free planner:

```powershell
python -m ziglang cc -std=c99 -O2 tools\catq_plan.c -o catq_plan.exe
```

The activation manifest is tab-separated; `view_spec` is optional and is useful
for selecting one expert from a folded MoE tensor:

```text
weight_name<TAB>calibration_shard<TAB>calibration_tensor<TAB>output_shard<TAB>view_spec
```

Generate and compile a Qwen3.5 plan:

```powershell
.\catq_plan.exe qwen3.5 C:\models\Qwen3.5 `
  activations.tsv qwen35-catq.pc
python picoscript_build.py native qwen35-catq.pc --provider catq
```

The Qwen3.5 adapter includes language-model projection matrices and excludes
layer norms, biases, convolution state, routing gates, embeddings, the vision
tower, and other tensors that should not be ternarized.

The GPT-OSS adapter accepts ordinary FP16/BF16 attention projections and pairs
`*_blocks` with their matching `*_scales` tensors for dependency-free MXFP4
dequantization. The native provider implements OpenAI's format directly:
32 E2M1 values per block, two values per byte, with an unsigned E8M0 exponent
scale biased by 127. For MoE tensors, use `view_spec` such as
`row_start=0;row_count=2880` together with expert-routed calibration activations.

GPT-OSS conversion is necessarily MXFP4 -> float reconstruction -> ternary,
rather than CAT-Q directly from the unavailable original high-precision expert
weights. The generated model should therefore be evaluated independently for
compound quantization loss.

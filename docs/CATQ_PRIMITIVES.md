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

# CAT-Q accelerator primitives

PicoScript exposes CAT-Q as coarse host-backed superinstructions. The language
orchestrates model shards and opaque tensor handles; CUDA, QPU, NEON, CPU, or
another host backend owns tensor storage, differentiation, optimization, and
packing.

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

`CatQ.Optimize` is deliberately coarse. A backend may use analytical gradients,
autodiff, CUDA kernels, a QPU implementation, or CPU code without exposing that
machinery to PicoScript.

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
pv_compute_hook = accelerator_hook;
pv_net_install_socket_provider();
```

All provider functions receive the VM context, hook code, and two operand
registers. Returning non-zero means the provider handled the operation.

## Example

```c
int shard = Shard.Load("model-00001.safetensors", "mmap");
int weights = Tensor.Map(shard, "dtype=bf16");
int calibration = Tensor.Map("calibration.bin", "shape=512x2048");
int catq = CatQ.Calibrate(calibration, "group=128;epochs=60");
int optimized = CatQ.Optimize(catq, weights);
int ternary = CatQ.Ternarize(catq, optimized);
int packed = CatQ.Pack(catq, ternary);
Shard.Save(packed, "model-00001.ternary");
```

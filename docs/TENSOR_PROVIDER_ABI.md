# Tensor and Accelerator Provider ABI

This contract is versioned independently of application model policy. Tensor,
BitLinear, CAT-Q, model, and shard providers use the same descriptor, handle,
request, and status rules; no LISWG-specific instructions are part of the ISA.

## `PTEN` descriptors

Tensor views are described by a little-endian `PTEN` span:

```text
magic[4] = "PTEN"
version:u8 = 1
dtype:u8
rank:u8
flags:u8
byte_offset:u32
byte_length:u32
dimensions[rank]:u32
strides[rank]:i32
```

The maximum rank is 8. Dimensions are positive, the stride count equals the
rank, and the descriptor has no trailing bytes. Dtype values are:

| Value | Type |
| ---: | --- |
| 1 | signed INT8 |
| 2 | UINT8 |
| 3 | signed INT32 |
| 4 | FP16 |
| 5 | BF16 |
| 6 | Q16.16 |

`Tensor.View` is a borrowed view over a retained parent. `Tensor.Materialize`
is the explicit operation that creates an owned contiguous copy. Views never
implicitly resize, copy, or reinterpret their parent.

## Opaque handles

Handles are non-zero 32-bit values containing a slot and generation. Providers
must reject stale generations with `INVALID_HANDLE`; release is idempotent
from the caller's perspective and does not resurrect a previous object.
`Tensor.Map` and `Shard.Load` return owned handles, `Tensor.View` returns a
retained borrowed handle, and `Tensor.Release` ends ownership.

## Provider request context

Every provider call has a bounded request context:

```c
typedef struct {
    uint32_t workspace_ptr;
    uint32_t workspace_bytes;
    uint32_t workspace_limit;
    uint32_t deadline_ticks;
    uint32_t cancel_token;
    uint32_t capability_mask;
    uint8_t cancelled;
} pv_tensor_request;
```

Python and JavaScript expose equivalent `ProviderRequest` values. Providers
must reject insufficient workspace before mutating outputs, check cancellation
at bounded points, enforce deadlines, and avoid hidden allocation. A provider
with no required workspace may leave the limit at zero.

## Status values

`Status.Last` uses these values for tensor and accelerator operations:

| Value | Meaning |
| ---: | --- |
| 0 | `OK` |
| 1 | `INVALID_HANDLE` / `NOT_FOUND` |
| 2 | `INVALID_ARGUMENT` |
| 3 | `EMPTY` |
| 7 | `ALLOCATION_FAILED` |
| 8 | `UNSUPPORTED_DEVICE` |
| 9 | `CANCELLED` |
| 10 | `TIMEOUT` |
| 11 | `WORKSPACE_EXHAUSTED` |
| 12 | `FORMAT_MISMATCH` |
| 13 | `SHARD_CORRUPT` |

INT8 kernels saturate to `[-128, 127]`; INT32 kernels use signed big-endian
elements and deterministic overflow behavior. Quantized tensors carry scale,
zero-point, group-size, and group-count metadata alongside the descriptor.

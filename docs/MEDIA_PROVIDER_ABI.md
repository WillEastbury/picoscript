# Media provider ABI

The portable media reference path is byte-oriented and deterministic. `Media`
shape is configured as positive `width` and `height` with a maximum of
16,384×16,384 pixels and a 16 MiB frame bound. Every frame span must contain
at least `width * height` bytes; malformed or truncated inputs return
`INVALID_ARGUMENT` without publishing an output.

`GrayDeltaEncode` and `GrayDeltaDecode` operate independently per row.
Residual/restore operations use modulo-256 byte arithmetic, and XOR
residual/restore uses byte XOR. These semantics are shared by Python, JS, and
C reference implementations.

`HasAccel` and `HasHevc` report capability only; they never imply that a
provider is installed. HEVC configure/decode returns explicit
`UNSUPPORTED_DEVICE` when no decoder provider is bound. Accelerated providers
must use the tensor/provider workspace and cancellation contract and publish
outputs only after successful validation.

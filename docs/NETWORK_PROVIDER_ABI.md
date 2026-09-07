# Network provider ABI

The portable C VM owns the hook ABI. Native POSIX/Windows and PIOS providers,
the Python reference provider, and injected JavaScript providers implement the
same logical contract.

## Request limits

Each request has:

```text
timeout_ms:u32
max_read_bytes:u32
pool_limit:u32
cancel_token:u32
cancelled:u8
```

`max_read_bytes` is a hard upper bound for one receive operation. A receive may
return fewer bytes without indicating failure. A send returns the number of
bytes accepted and providers must not hide partial writes behind a different
result shape. A cancelled operation completes without publishing a new
handle or span.

## Handles and statuses

Socket handles are provider-owned opaque integers. Handle `0` is invalid;
closed, unknown, and exhausted handles must not be reused as successful
operations.

| Value | Meaning |
| ---: | --- |
| 0 | `OK` |
| 1 | `INVALID_HANDLE` |
| 2 | `INVALID_ARGUMENT` |
| 3 | `TIMEOUT` |
| 4 | `DISCONNECTED` |
| 5 | `UNAVAILABLE` / provider I/O failure |
| 6 | `ADDRESS_ERROR` |
| 7 | `WOULD_BLOCK` |
| 8 | `CANCELLED` |
| 9 | `POOL_EXHAUSTED` |

`Listen`, `Accept`, `Connect`, `Read`/`RecvSpan`, `Write`/`SendSpan`, and
`Shutdown` all update `Status.Last`. `PoolSize` reports the configured
connection budget, while `Register` installs a provider-owned listener/event
lane rather than silently returning success without a resource.

## Platform adapters

- `vm/picovm_net.c` is the hosted POSIX/Windows implementation.
- `host/pv_host_pios.c` maps the same hooks to kernel socket services when
  `PIOS_PLATFORM` or `PIOS_USER_EL0` is enabled.
- `SocketNetworkProvider` is the synchronous Python reference adapter.
- JavaScript providers receive `vm.networkRequest` and must implement the same
  status and partial-I/O rules; browser transports remain injected because
  browser and Node event loops differ.

HTTP provider work consumes this transport contract rather than defining a
second socket or timeout model.

## Datagram binding

The providers expose bounded UDP operations:

| Hook | Inputs | Result |
|---|---|---|
| `Net.DatagramBind(port)` | local UDP port, `0` for ephemeral | opaque datagram handle |
| `Net.DatagramRecv(handle, max_bytes)` | handle and receive cap | payload span; records the source peer |
| `Net.DatagramPeer(handle)` | handle | six-byte endpoint span: IPv4 network-order bytes plus port |
| `Net.DatagramSetPeer(handle, endpoint)` | handle and endpoint span | `1` on success |
| `Net.DatagramSend(handle, payload)` | handle and payload span | accepted byte count |
| `Net.DatagramClose(handle)` | handle | `1` on success |

The endpoint span is exactly four IPv4 bytes followed by a two-byte
network-order port. Receive lengths are clamped by `max_read_bytes`, and all
operations use the existing timeout, cancellation, opaque-handle, and
`Status.Last` rules. Providers must not expose raw socket pointers or reuse a
closed handle successfully.

DHCP/PXE services can construct an explicit peer endpoint in script. DHCP
authorization remains script-visible and should default-deny unknown client
MACs; capture mode may record pending MACs without granting a lease.

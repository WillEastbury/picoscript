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

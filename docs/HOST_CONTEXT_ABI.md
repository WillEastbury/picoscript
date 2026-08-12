# Card, Context, and Environment provider ABI

These providers are optional seams below the shared VM hook ABI. The portable
C VM and PIOS may install `pv_card_hook` and `pv_context_hook`; Python and
JavaScript accept `card_provider`, `context_provider`, and
`environment_provider` injections. A missing provider uses an explicit
unavailable result and never a success-shaped fabricated value.

## Card provider

`Card.Read` returns a bounded caller-visible span, `Card.Write` consumes a
bounded span, and `Card.Address` returns an opaque device/card handle. Card
handles are not memory pointers and must not be serialized into application
data. Providers must enforce capability checks before exposing device identity,
return `INVALID_ARGUMENT` for malformed spans, and use `NOT_FOUND` for absent
cards.

## Request context

Context values are request-scoped and span-based. Providers must cap headers,
query strings, bodies, identity names, and trace IDs before copying them into
the VM arena. A context span remains valid only for the current request and
must be invalidated on request reset. Sensitive fields (`GetUser`,
`GetPermissions`, and `GetClientCert`) require the corresponding capability;
denial and unavailable providers are distinct statuses.

## Environment

Environment values are bounded snapshots, not reflection or unbounded
enumeration. Providers expose only declared facts such as OS version,
hostname, timezone, CPU count, memory totals, process ID, and elapsed time.
PIOS obtains these through kernel IPC/SVC; hosted providers use the platform
adapter. Unknown facts return `UNAVAILABLE`, never arbitrary host defaults.

## Status and lifetime

All three provider families use the common status convention:

| Value | Meaning |
| ---: | --- |
| 0 | `OK` |
| 1 | `NOT_FOUND` |
| 2 | `INVALID_ARGUMENT` |
| 5 | `UNAVAILABLE` |
| 8 | `CAPABILITY_DENIED` |
| 12 | `REDACTED` |
| 13 | `TRUNCATED` |

The exact hook-specific status remains available through `Status.Last`.
Providers must not retain VM span pointers after the hook returns.

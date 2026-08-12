# Identity, trust, and certificate provider ABI

Auth and X509 providers are injected through the C/PIOS host provider seam or
the `identity_provider` option on the Python and JavaScript VMs. Credentials
and private keys remain provider-owned; PicoScript receives only bounded
authorized spans or opaque key handles.

Provider operations cover credential validation, principals, permissions,
tokens, certificate retrieval/storage, chain validation, expiry, revocation,
and key handles. Successful authentication and certificate operations must
set `Status.Last` to zero; missing credentials/trust, expired or revoked
tokens, invalid chains, denied capabilities, and unavailable providers use
distinct non-zero statuses.

Providers must not copy passwords or private key material into persistent VM
state. Token and certificate spans are request-scoped unless explicitly
materialized by the caller. Capability checks occur before secret access, and
audit-capable providers should record authentication, key, certificate, and
revocation decisions without exposing secret values.

The hosted C implementation is `host/pv_auth_store.c`; PIOS uses the secure
store/X509 hooks in `host/pv_host_pios.c` when `PIOS_PLATFORM` is enabled.
Null, Python, and JavaScript providers intentionally return explicit
unavailable/denied statuses rather than fabricated identities.

# Provider conformance matrix

`docs/PROVIDER_MANIFEST.json` is the source of truth for host-provider
capabilities. It describes ownership, limits, security requirements, and an
explicit status for every `HOST_HOOK_CODES` entry on each deployment target.

Generate the current machine-readable report with:

```powershell
python tools/provider_conformance.py --json
```

The report is deliberately target-oriented:

- `implemented` means deterministic reference semantics require no provider;
- `provider-backed` means a provider must be installed and its manifest is
  authoritative for limits and ownership;
- `unsupported` means the target returns a defined unsupported status;
- `intentionally-unavailable` means the deployment policy denies the feature.

The null target converts every provider-backed operation to `unsupported`
instead of silently passing it. PIOS remains provider-backed where the
kernel-IPC skeleton owns the binding; individual unavailable operations must
return the documented status rather than a success-shaped zero.

The conformance unit test asserts that every hook is represented for every
target. Runtime fixture suites remain the behavioral proof for deterministic
Python/JavaScript/C paths and for hosted native providers.

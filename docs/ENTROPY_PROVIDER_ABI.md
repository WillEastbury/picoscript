# Deterministic entropy provider ABI

`Maths.Random` and `Maths.RandomRange` use a seeded 32-bit xorshift stream in
the reference Python, JavaScript, and C VMs. The seed and state are unsigned
32-bit values; each step applies:

```text
x ^= x << 13
x ^= x >> 7
x ^= x << 17
```

with 32-bit masking after every operation. `Random` returns the upper 16 bits
as a non-negative integer. `RandomRange(lo, hi)` swaps reversed bounds, uses an
inclusive range, and uses rejection-free modulo arithmetic for the bounded
32-bit result. A zero-width overflow range returns the lower bound.

Seeded operation is deterministic and requires no live provider. Hardware or
OS entropy is a separate capability-gated provider; when it is not installed,
the VM returns `UNAVAILABLE` rather than silently substituting wall-clock or
process state. Fixed-seed fixtures must compare the complete byte sequence
across Python, JavaScript, portable C, native C, and native JavaScript.

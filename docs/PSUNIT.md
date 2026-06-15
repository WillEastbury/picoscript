# PSUnit — a PicoScript-authored test harness

PSUnit lets you write unit and smoke tests **in PicoScript itself** and run them on
the reference VM (and, in `--parity` mode, on the JS VM as well). It is the testing
counterpart to the device/capsule program: a test can seed the provider seams
(`Storage.*` cards, `Gpio.*` pins, `Device.*`/`Stream.*` rings) and assert on them,
so PSUnit doubles as a smoke test for the browser/sim runtime.

## The `Assert.*` namespace

`Assert.*` is a host-injected namespace (like `Gpio.*`/`Stream.*`): the VM keeps two
counters per run. It is deterministic integer logic, so the Python VM and the JS VM
stay byte-identical. No capability is required (it is harness state, not a security
surface).

| Hook | Code | In → Out | Effect |
|------|------|----------|--------|
| `Assert.Eq(actual, expected)` | `0x0178` | `a, b → 1/0` | total++; fail++ unless `a == b` |
| `Assert.True(cond)`           | `0x0179` | `c → 1/0`   | total++; fail++ unless `c != 0` |
| `Assert.Count()`              | `0x017A` | `→ total`   | number of assertions made |
| `Assert.Failed()`             | `0x017B` | `→ failed`  | number that failed |
| `Assert.Reset()`              | `0x017C` | `→ 0`       | clears both counters |

A test body is *just assertions* — the harness reads `Count()`/`Failed()` out of the
host to decide pass/fail, so you never write reporting boilerplate.

## Writing a test

Tests live in `tests/psunit/` and are picked up by file extension, so you can use any
of the four frontends:

**C (`.pc`)**
```c
int a = 2 + 3;
Assert.Eq(a, 5);
Assert.True(a > 0);
```

**Python (`.ppy`)**
```python
a = 6
b = 7
Assert.Eq(a * b, 42)
Assert.True(a < b)
```

**English (`.eng`)**
```
Set a to 20.
Set b to 22.
Assert.Eq(a + b, 42).
```

**BASIC (`.pbas`)** — use the idiomatic `ASSERT` keyword (it takes any condition; in
BASIC it shadows a dotted `Assert.*` call, exactly like `GPIO` shadows `Gpio.*`):
```basic
DIM a = 6
DIM b = 7
ASSERT a * b = 42
ASSERT a < b
ASSERT a <> b
```

`ASSERT <cond>` is sugar for `Assert.True(<cond>)`; the condition is an ordinary BASIC
expression (`=`, `<>`, `<`, `>`, `AND`/`OR`), evaluated to `0/1`.

### Seeding the provider seams

A test can set up state and then assert on it — for example, seed three cards and
query them:

```c
int qty = "qty";
Storage.UsePack(1024);
int a = Storage.AddCard(); Storage.SetField(qty, 42);
int b = Storage.AddCard(); Storage.SetField(qty, 7);
Storage.EditCard(a);
Assert.Eq(Storage.GetField(qty), 42);
```

## Running tests

```sh
python psunit.py                 # run tests/psunit/* on the Python VM
python psunit.py --parity        # also require Python VM == JS VM (needs node)
python psunit.py tests/psunit/core.pc tests/psunit/cards.pc   # specific files
```

Output:
```
  tests\psunit\cards.pc           ok   5 asserts
  tests\psunit\core.pc            ok   6 asserts
  ...
psunit: 6/6 files passed, 27 assertions [parity]
```

The runner exits non-zero if any file fails, has zero assertions, or (with
`--parity`) diverges between the Python and JS VMs.

### In the browser

The editor (`docs/playground.html`) runs PSUnit on the JS VM directly: after **Compile
& Run**, an assertion badge reports `✓ N/N assertions passed` or `✗ K/N FAILED`. Load
the **Testing → PSUnit assertions** sample to try it.

## In CI

`tests/test_psunit.py` is part of the normal suite. It runs every `tests/psunit/*`
file (with parity when `node` is available), proves a failing assertion is detected,
checks `Assert.*` counters are byte-identical Python↔JS, and verifies the BASIC
`ASSERT` keyword lowers to the same bytecode as the canonical `Assert.True` spelling
(and that the Python frontend matches `vm/picoc.js`).

## Adding more assertions

`Assert.*` lives in `picoscript_lang.py` (`HOST_HOOK_CODES`, codes `0x0178–0x017C`).
To extend it, follow the standard hook workflow: add the code, run `gen_hooks_js.py`,
implement `_assert` in `picoscript_vm.py` and the mirror in `vm/picovm.js`, keep
`Assert` allowlisted in `tests/test_parity_gate.py`, and add coverage to
`tests/test_psunit.py`. Keep the Python and JS implementations byte-identical.

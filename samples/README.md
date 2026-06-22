# samples — capability & performance experiments

Trial rewrites that test how far PicoScript can build real systems software, and
surface what the language/runtime are still missing.

| Sample | Goal | Result |
|--------|------|--------|
| [`picoweb-in-picoscript/`](./picoweb-in-picoscript/) | an HTTP server (parse → route → respond) in PicoScript | ✅ works on the VM; ~3.5× faster with warm VM reuse |
| [`picowal-in-picoscript/`](./picowal-in-picoscript/) | a WAL-backed pack/card store with crash recovery in PicoScript | ✅ works on the VM incl. WAL replay |

**Read [`FINDINGS.md`](./FINDINGS.md)** for the consolidated gap analysis: the
two bugs found (both fixed here), the ergonomic gaps, and — most importantly for
a database server — the missing hardware bindings (**UART, SPI/I²C, PCIe,
M.2/NVMe block storage**).

Each sample runs on the bytecode VM via its `*_demo.py` harness:

```
python picoweb-in-picoscript/run_demo.py
python picowal-in-picoscript/store_demo.py
```

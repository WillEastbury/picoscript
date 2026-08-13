# Storage façade parity

The reusable storage façade has two compatible layers:

- `Storage.*` remains the legacy active-pack/card API;
- `Db.*` provides explicit-pack CRUD, query, cursor, and plan operations.

Both layers use the same card encoding, schema IDs, status values, pack
isolation, recovery generations, and Definition overlay metadata. The native
PicoWAL adapter may select HASH/ORDERED/FULLTEXT/GRAPH/SCAN paths, while
reference providers use deterministic in-memory equivalents.

The parity fixture covers schema registration, insert/read/update/patch/delete,
query results, cursor invalidation, Definition resolution, and reopen behavior.

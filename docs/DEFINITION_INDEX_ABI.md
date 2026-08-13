# Definition index ABI

`DefinitionRegistry` stores deterministic index metadata while ordinary cards
remain the logical storage model. Index IDs are the first 16 hex digits of a
canonical SHA-256 over pack, kind, field ID, and schema version.

Supported kinds are `HASH`, `ORDERED`, `FULLTEXT`, `POSITIONAL`, `GRAPH_NODE`,
`GRAPH_EDGE`, and `GRAPH_WEIGHT`. Registration is idempotent, removal and
lookup return explicit `NOT_FOUND`, and rebuild advances the sealed overlay
generation. Providers persist the metadata and validate the generation/CRC of
their overlay pages during reopen; stale or corrupt pages must rebuild from
ordinary cards rather than becoming authoritative.

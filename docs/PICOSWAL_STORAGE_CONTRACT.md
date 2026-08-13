# PicoWAL Storage Contract

The reusable storage contract keeps packs, schemas, and cards as ordinary
provider records. The reference `PicoStore` uses an in-memory backend or the
`JournalBackend`; native and PIOS providers implement the same logical
operations over PicoWAL/WALFS.

## Pack and schema lifecycle

Pack identifiers are stable caller-supplied values. Creating an existing pack
is a duplicate error; operations on an unknown pack create it only through the
legacy `Storage.*` compatibility path. A schema is registered with a positive
version and a deterministic identifier derived from the canonical pack,
version, and field definition. Replacing a schema requires an explicitly
higher version and migration permission; incompatible changes otherwise return
`CONFLICT`.

A schema-less pack has the implicit fixed record shape `blobCard`:
`@id(0) int id` and `@id(1) byte[] data`. The pack manifest stores
`max_card_bytes` (4 KiB by default for new reference packs); writes above that
bound fail before serialization. The payload is exposed as a lazy bounded span
view, so decoding a blobCard does not copy its data unless the caller requests
a record copy.

## CRUD

`Insert`/`AddCard` allocates the next unused positive card ID. `Read` returns a
caller-visible card view or `NOT_FOUND`. `Write`/`Update` replace an existing
card, `Patch` applies fields to an existing card, and `Delete` removes it.
Duplicate IDs, missing cards, invalid pack/card IDs, schema violations, and
corrupt serialized payloads are explicit failures; no operation silently
creates a missing card on update.

Status values are stable across providers:

| Value | Meaning |
| ---: | --- |
| 0 | `OK` |
| 1 | `NOT_FOUND` |
| 2 | `INVALID` |
| 3 | `DUPLICATE` |
| 4 | `CONFLICT` |
| 5 | `CORRUPT` |

## Durability and recovery

`JournalBackend` publishes the complete key/value state through a generation
commit on `CheckpointJournal` or `DurableCheckpointJournal`. Records are CRC
protected; reopen selects the last complete generation and quarantines a torn
tail. `Sync` flushes the provider and `Recover` reloads the last valid
generation. File/native and PIOS/WALFS adapters may use the same generation and
CRC rules without changing the public CRUD contract.

The `Db.*` CRUD façade uses explicit pack IDs and maps to provider storage:
`Insert(pack, span)`, `Read(pack, card)`, `Write/Update/Patch(pack, card,
span)`, `Delete(pack, card)`, `Sync()`, and `Recover()`. For the compact
two-input/one-output ABI, write operations use the destination register as the
input span and return their status in that register.

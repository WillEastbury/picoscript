# Deterministic query IR

`Query` is lowered to a canonical versioned IR before a provider chooses an
access path. Canonicalization flattens and sorts commutative `AND`/`OR`
children, removes double negation, and serializes with sorted JSON keys.

The IR contains:

```text
version, pack, schema, predicate, projection, order, limit
```

Predicates use `EQ`, `NE`, `LT`, `LTE`, `GT`, `GTE`, `AND`, `OR`, and `NOT`
nodes with explicit `FIELD` and `CONST` operands. The planner chooses, in
deterministic order, a matching HASH, ORDERED, FULLTEXT, GRAPH, or SCAN path.
The selected index predicate is removed from `residual`; all remaining
predicates are preserved for provider-side filtering.

PicoWAL's existing exact, ordered, full-text, and graph indexes remain the
execution substrate. The IR/planner is a compatibility layer above them and
does not introduce a new ISA instruction or storage engine.

The native adapter reports the selected access path through `Db.Plan`:
`1=HASH`, `2=ORDERED`, `3=FULLTEXT`, `4=GRAPH`, and `5=SCAN`. Legacy string
queries deliberately report `SCAN`; structured planner integration can replace
that parser without changing the underlying index implementations.

# Graph resource limits

The portable PicoWAL graph provider uses fixed storage and traversal budgets:

- graph slots, edge cards, result rows, and text/index pages have compile-time
  maxima;
- shortest-path traversal receives caller-owned node, distance, parent, and
  closed-state work arrays;
- repeated nodes are coalesced by `work_find`, and closed nodes are never
  expanded twice;
- negative edge weights are ignored by the non-negative shortest-path
  provider;
- dense/cyclic graphs terminate when the work capacity is exhausted.

Graph operations never grow an unbounded heap structure. A complete result sets
`Status.Last` to `0`; a valid path discovered before the work budget is
exhausted is returned with `Status.Last = 3` (`PARTIAL`); invalid definitions
and missing graphs use the normal invalid/not-found statuses. Capability checks
remain in the VM before graph/index mutation hooks.

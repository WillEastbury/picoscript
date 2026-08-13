# Typed cursor ABI

Typed cursors retain only bounded plan/scan state and one current record view.
`Next` invalidates the previous `Current`; `Reset`, `Close`, remap, or
destruction also invalidate it. `Current` is a view and never implicitly
persists mutations. `CopyCurrent` is the explicit caller-owned copy operation.

Status values are `0=OK`, `1=NOT_FOUND/CLOSED`, `2=INVALID`, and `3=EOF`.
The cursor enforces a maximum row budget and returns deterministic exhaustion
instead of growing an unbounded result list. Providers may use index, full-text,
graph, or scan plans, but all expose the same lifetime and copy semantics.

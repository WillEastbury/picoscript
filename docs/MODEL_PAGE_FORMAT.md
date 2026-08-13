# Immutable model-page format

Model overlays use versioned `PMPG` pages with a 16 KiB logical/encoded bound.
Entries are sorted by raw key bytes before encoding. The payload is compressed
with deterministic raw DEFLATE only when compression reduces size; otherwise
the uncompressed payload is retained.

Each page records checksums over both packed and decoded payloads, entry count,
logical/encoded lengths, and generation. Pages are immutable. Updates append a
replacement page and publish a new root containing page checksums and a higher
generation. Cold reload validates every page and root checksum before exposing
entries; corrupt or incompatible pages are rejected and must be rebuilt from
the logical PicoWAL cards.

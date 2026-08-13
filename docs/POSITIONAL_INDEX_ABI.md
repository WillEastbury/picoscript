# Positional full-text overlay ABI

`PPOS` pages store a sorted UTF-8 term dictionary with document and token
positions encoded as unsigned deltas. Pages are immutable, CRC-protected, and
bounded to 16 KiB. Rebuilds publish a new generation over ordinary cards.

Modes are `ANY=0`, `AND=1`, `PHRASE=2`, and `NEAR=3`; NEAR distance is capped
at 64. Results are capped at 4096 IDs. Corrupt pages, invalid terms, oversized
documents, and unsupported modes fail explicitly. The C/PIOS provider should
use the same page format and status values before JavaScript/Python providers
are treated as parity authorities.

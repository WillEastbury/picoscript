# Native Parquet decoding

The dependency-free reader now covers the metadata path and retained page
body primitives:

1. Compact-Thrift primitives and bounded containers;
2. `FileMetaData` footer validation and decoding;
3. schema and row-group descriptors;
4. page-header sizes/types and nested data-page headers;
5. plain values and RLE/bit-packed hybrid values.

All readers enforce input, nesting, container, value-count, and output bounds.
Compression codecs and dictionary/page-index integration remain provider
extensions; malformed or unsupported encodings return explicit `ParquetError`
instead of partially materialized rows.

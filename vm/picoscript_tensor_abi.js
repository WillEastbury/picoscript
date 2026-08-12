"use strict";

const MAGIC = Buffer.from("PTEN");
const DTYPE_MAX = 6;

function decode(data) {
  const b = Buffer.from(data);
  if (b.length < 16 || !b.subarray(0, 4).equals(MAGIC)) {
    throw new Error("tensor descriptor magic/length mismatch");
  }
  const version = b[4], dtype = b[5], rank = b[6], flags = b[7];
  if (version !== 1 || dtype < 1 || dtype > DTYPE_MAX || rank < 1 || rank > 8) {
    throw new Error("tensor descriptor format mismatch");
  }
  if (b.length !== 16 + rank * 8) throw new Error("tensor descriptor length mismatch");
  const dimensions = [], strides = [];
  let pos = 16;
  for (let i = 0; i < rank; i++, pos += 4) {
    const value = b.readUInt32LE(pos);
    if (value === 0) throw new Error("tensor dimension must be positive");
    dimensions.push(value);
  }
  for (let i = 0; i < rank; i++, pos += 4) strides.push(b.readInt32LE(pos));
  return {
    version, dtype, rank, flags,
    byteOffset: b.readUInt32LE(8), byteLength: b.readUInt32LE(12),
    dimensions, strides
  };
}

module.exports = { decode };

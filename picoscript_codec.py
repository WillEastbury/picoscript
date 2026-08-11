"""Bounded adapter over PicoScript's canonical deflate codec."""

from picoscript_vm import _deflate, _inflate


class CodecError(ValueError):
    pass


def compress(data: bytes, capacity=None):
    raw = bytes(data)
    encoded = _deflate(raw)
    if capacity is not None and capacity < len(encoded):
        raise CodecError("output buffer too small")
    return encoded


def compress_size(data: bytes):
    return len(_deflate(bytes(data)))


def decompress(data: bytes, capacity=None):
    try:
        decoded = _inflate(bytes(data))
    except Exception as exc:
        raise CodecError("malformed compressed input") from exc
    if capacity is not None and capacity < len(decoded):
        raise CodecError("output buffer too small")
    return decoded

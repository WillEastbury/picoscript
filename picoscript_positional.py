"""Bounded deterministic positional full-text overlay reference."""

from dataclasses import dataclass
import struct
import zlib

MAGIC = b"PPOS"
VERSION = 1
MAX_TERMS = 16384
MAX_DOCUMENTS = 65536
MAX_RESULTS = 4096
MAX_NEAR = 64


class PositionalError(ValueError):
    pass


def _varint(value):
    value = int(value)
    if value < 0:
        raise PositionalError("negative varint")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _read_varint(data, pos):
    value = 0
    shift = 0
    while True:
        if pos >= len(data) or shift > 28:
            raise PositionalError("truncated positional varint")
        byte = data[pos]; pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _tokens(text):
    return [token for token in str(text).lower().replace("_", " ").split() if token]


@dataclass(frozen=True)
class PositionalPage:
    encoded: bytes
    generation: int

    @classmethod
    def seal(cls, postings, *, generation=1):
        if len(postings) > MAX_TERMS:
            raise PositionalError("term dictionary exceeds limit")
        body = bytearray()
        for term in sorted(postings):
            raw = term.encode("utf-8")
            if len(raw) > 255:
                raise PositionalError("term exceeds limit")
            body.append(len(raw)); body.extend(raw)
            docs = postings[term]
            body.extend(_varint(len(docs)))
            last_doc = 0
            for doc, positions in sorted(docs.items()):
                body.extend(_varint(doc - last_doc)); last_doc = doc
                body.extend(_varint(len(positions)))
                last_pos = 0
                for position in positions:
                    body.extend(_varint(position - last_pos)); last_pos = position
        if len(body) > 16 * 1024:
            raise PositionalError("positional page exceeds 16 KiB")
        header = struct.pack(
            "<4sBBHIII", MAGIC, VERSION, 0, 0, len(postings),
            zlib.crc32(body) & 0xFFFFFFFF, int(generation),
        )
        return cls(header + body, int(generation))

    @classmethod
    def open(cls, encoded):
        raw = bytes(encoded)
        if len(raw) < 20:
            raise PositionalError("positional page truncated")
        magic, version, flags, reserved, term_count, checksum, generation = struct.unpack_from("<4sBBHIII", raw)
        if magic != MAGIC or version != VERSION or flags != 0 or reserved != 0:
            raise PositionalError("positional page format mismatch")
        body = raw[20:]
        if term_count > MAX_TERMS or zlib.crc32(body) & 0xFFFFFFFF != checksum:
            raise PositionalError("positional page checksum/term limit failure")
        page = cls(raw, generation)
        page._decode(body, term_count)
        return page

    def _decode(self, body, expected_terms=None):
        result = {}
        pos = 0
        while pos < len(body):
            length = body[pos]; pos += 1
            if pos + length > len(body):
                raise PositionalError("truncated term")
            term = body[pos:pos + length].decode("utf-8"); pos += length
            doc_count, pos = _read_varint(body, pos)
            docs = {}; last_doc = 0
            for _ in range(doc_count):
                delta, pos = _read_varint(body, pos)
                doc = last_doc + delta; last_doc = doc
                count, pos = _read_varint(body, pos)
                positions = []; last_position = 0
                for _ in range(count):
                    delta, pos = _read_varint(body, pos)
                    last_position += delta; positions.append(last_position)
                docs[doc] = positions
            result[term] = docs
        if expected_terms is not None and len(result) != expected_terms:
            raise PositionalError("positional term count mismatch")
        return result

    def postings(self):
        return self._decode(self.encoded[20:])


class PositionalIndex:
    def __init__(self):
        self.documents = {}
        self.generation = 0
        self.page = None

    def upsert(self, document_id, text):
        document_id = int(document_id)
        if document_id < 0 or document_id >= MAX_DOCUMENTS:
            raise PositionalError("document ID exceeds limit")
        self.documents[document_id] = _tokens(text)
        return self.rebuild()

    def delete(self, document_id):
        self.documents.pop(int(document_id), None)
        return self.rebuild()

    def rebuild(self):
        postings = {}
        for document, tokens in sorted(self.documents.items()):
            for position, token in enumerate(tokens):
                postings.setdefault(token, {}).setdefault(document, []).append(position)
        self.generation += 1
        self.page = PositionalPage.seal(postings, generation=self.generation)
        return self.page

    def query(self, text, mode=0, near_distance=0):
        terms = _tokens(text)
        if not terms or self.page is None:
            return []
        postings = self.page.postings()
        candidates = set()
        for term in terms:
            candidates.update(postings.get(term, {}))
        if mode == 1:
            candidates = set.intersection(*(set(postings.get(term, {})) for term in terms)) if terms else set()
        elif mode == 2:
            candidates = {doc for doc in candidates if self._phrase(doc, terms, postings)}
        elif mode == 3:
            distance = min(MAX_NEAR, max(1, int(near_distance)))
            candidates = {doc for doc in candidates if self._near(doc, terms, postings, distance)}
        return sorted(candidates)[:MAX_RESULTS]

    @staticmethod
    def _phrase(doc, terms, postings):
        starts = set(postings.get(terms[0], {}).get(doc, ()))
        for offset, term in enumerate(terms[1:], 1):
            starts &= {pos - offset for pos in postings.get(term, {}).get(doc, ())}
        return bool(starts)

    @staticmethod
    def _near(doc, terms, postings, distance):
        positions = [postings.get(term, {}).get(doc, ()) for term in terms]
        return bool(positions and all(any(abs(a - b) <= distance for b in other)
                                      for a in positions[0] for other in positions[1:]))

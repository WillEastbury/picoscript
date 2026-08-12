"""Provider-independent atomic PicoWAL artifact persistence primitives.

Records are self-validating and commits are published only after all records
and the commit marker have been flushed.  Providers can map these operations
to PicoWAL pages or file/WALFS blocks without changing recovery semantics.
"""

from dataclasses import dataclass
import hashlib
import json
import struct
import zlib
import os

MAGIC = b"PSCP"
VERSION = 1
HEADER = struct.Struct("<4sBBHQQI")  # magic, version, kind, reserved, generation, key, length
COMMIT = 1
PAGE = 2
MANIFEST_MAGIC = b"PSAM"
MANIFEST_VERSION = 1
ARTIFACT_KINDS = frozenset({
    "model_page", "shard_manifest", "statistics", "provenance", "checkpoint",
})


@dataclass(frozen=True)
class Recovery:
    generation: int
    pages: dict
    valid_end: int
    quarantined: bool


@dataclass(frozen=True)
class ArtifactManifest:
    """Immutable metadata published alongside one logical artifact."""

    artifact_id: str
    kind: str
    generation: int
    format_version: int
    page_count: int
    total_bytes: int
    checksum: int
    dependencies: tuple = ()
    page_keys: tuple = ()
    metadata: bytes = b""

    def encode(self):
        payload = {
            "artifact_id": self.artifact_id,
            "checksum": self.checksum,
            "dependencies": [[name, int(generation)] for name, generation in self.dependencies],
            "format_version": self.format_version,
            "generation": self.generation,
            "kind": self.kind,
            "metadata": bytes(self.metadata).hex(),
            "page_count": self.page_count,
            "page_keys": [int(key) for key in self.page_keys],
            "total_bytes": self.total_bytes,
        }
        return MANIFEST_MAGIC + json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @classmethod
    def decode(cls, payload):
        if not payload.startswith(MANIFEST_MAGIC):
            raise ValueError("not an artifact manifest")
        try:
            value = json.loads(payload[len(MANIFEST_MAGIC):].decode("utf-8"))
            dependencies = tuple(
                (str(item[0]), int(item[1])) for item in value["dependencies"]
            )
            page_keys = tuple(int(key) for key in value["page_keys"])
            manifest = cls(
                artifact_id=str(value["artifact_id"]),
                kind=str(value["kind"]),
                generation=int(value["generation"]),
                format_version=int(value["format_version"]),
                page_count=int(value["page_count"]),
                total_bytes=int(value["total_bytes"]),
                checksum=int(value["checksum"]) & 0xFFFFFFFF,
                dependencies=dependencies,
                page_keys=page_keys,
                metadata=bytes.fromhex(str(value.get("metadata", ""))),
            )
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("malformed artifact manifest") from exc
        if (
            not manifest.artifact_id
            or manifest.kind not in ARTIFACT_KINDS
            or manifest.generation < 1
            or manifest.format_version < 1
            or manifest.page_count < 0
            or manifest.total_bytes < 0
            or len(manifest.page_keys) != manifest.page_count
        ):
            raise ValueError("invalid artifact manifest")
        return manifest


@dataclass(frozen=True)
class ArtifactValidation:
    valid: bool
    stale: bool
    errors: tuple = ()


def _artifact_key(prefix, artifact_id, generation, page_index=0):
    """Derive a deterministic non-zero journal key without string parsing."""
    digest = hashlib.blake2b(
        prefix + b"\0" + artifact_id.encode("utf-8") +
        struct.pack("<QQ", int(generation), int(page_index)),
        digest_size=8,
    ).digest()
    key = struct.unpack("<Q", digest)[0]
    return key or 1


class ArtifactStore:
    """Reusable model/shard/statistics/checkpoint layer over a journal.

    The journal is the storage provider boundary: ``CheckpointJournal`` is an
    in-memory/PicoWAL-compatible provider and ``DurableCheckpointJournal`` is
    the file/WALFS-style provider.  The artifact layer never changes card
    semantics or requires a second storage engine.
    """

    def __init__(self, journal=None, *, max_page_bytes=16 * 1024,
                 supported_versions=(1,)):
        if max_page_bytes <= 0:
            raise ValueError("max_page_bytes must be positive")
        self.journal = journal if journal is not None else CheckpointJournal()
        self.max_page_bytes = int(max_page_bytes)
        self.supported_versions = frozenset(int(version) for version in supported_versions)

    def recover(self):
        return self.journal.recover()

    def _records(self):
        return self.recover().pages

    def _manifests(self, records=None):
        records = self._records() if records is None else records
        found = {}
        for payload in records.values():
            if not payload.startswith(MANIFEST_MAGIC):
                continue
            try:
                manifest = ArtifactManifest.decode(payload)
            except ValueError:
                continue
            found[(manifest.artifact_id, manifest.generation)] = manifest
        return found

    def manifest(self, artifact_id, generation=None):
        records = self._records()
        if generation is None:
            candidates = [
                item for (name, _), item in self._manifests(records).items()
                if name == artifact_id
            ]
            return max(candidates, key=lambda item: item.generation, default=None)
        return self._manifests(records).get((str(artifact_id), int(generation)))

    def publish(self, artifact_id, kind, pages, *, format_version=1,
                dependencies=(), metadata=b"", generation=None):
        artifact_id = str(artifact_id)
        if not artifact_id or len(artifact_id.encode("utf-8")) > 128:
            raise ValueError("artifact_id must be 1..128 UTF-8 bytes")
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"unsupported artifact kind: {kind}")
        format_version = int(format_version)
        if format_version not in self.supported_versions:
            raise ValueError(f"unsupported artifact format version: {format_version}")
        normalized_dependencies = tuple(
            sorted((str(name), int(dep_generation)) for name, dep_generation in dependencies)
        )
        page_values = []
        checksum = 0
        total_bytes = 0
        for page in pages:
            value = bytes(page)
            if len(value) > self.max_page_bytes:
                raise ValueError("artifact page exceeds configured bound")
            page_values.append(value)
            total_bytes += len(value)
            checksum = zlib.crc32(value, checksum) & 0xFFFFFFFF

        current = self.recover()
        expected_generation = current.generation
        target_generation = expected_generation + 1 if generation is None else int(generation)
        if target_generation <= expected_generation:
            raise ValueError("artifact generation must advance monotonically")
        page_keys = tuple(
            _artifact_key(b"page", artifact_id, target_generation, index)
            for index in range(len(page_values))
        )
        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            kind=kind,
            generation=target_generation,
            format_version=format_version,
            page_count=len(page_values),
            total_bytes=total_bytes,
            checksum=checksum,
            dependencies=normalized_dependencies,
            page_keys=page_keys,
            metadata=bytes(metadata),
        )
        records = dict(current.pages)
        records[_artifact_key(b"manifest", artifact_id, target_generation)] = manifest.encode()
        records.update(zip(page_keys, page_values))
        if not self.journal.compare_and_commit(expected_generation, target_generation, records):
            raise RuntimeError("artifact publish lost its generation compare-and-swap")
        return manifest

    def validate(self, artifact_id, generation=None, *, check_dependencies=True):
        records = self._records()
        manifest = self.manifest(artifact_id, generation)
        if manifest is None:
            return ArtifactValidation(False, False, ("missing manifest",))
        errors = []
        stale = False
        if manifest.format_version not in self.supported_versions:
            errors.append("unsupported format version")
        values = []
        for key in manifest.page_keys:
            if key not in records:
                errors.append(f"missing page {key}")
                continue
            page = records[key]
            if len(page) > self.max_page_bytes:
                errors.append(f"page {key} exceeds configured bound")
            values.append(page)
        if len(values) != manifest.page_count:
            errors.append("page count mismatch")
        if sum(len(page) for page in values) != manifest.total_bytes:
            errors.append("byte count mismatch")
        checksum = 0
        for page in values:
            checksum = zlib.crc32(page, checksum) & 0xFFFFFFFF
        if checksum != manifest.checksum:
            errors.append("artifact checksum mismatch")
        if check_dependencies:
            for dependency, required_generation in manifest.dependencies:
                dependency_manifest = self.manifest(dependency)
                if dependency_manifest is None:
                    errors.append(f"missing dependency {dependency}@{required_generation}")
                    stale = True
                elif dependency_manifest.generation != required_generation:
                    errors.append(
                        f"stale dependency {dependency}: "
                        f"expected {required_generation}, got {dependency_manifest.generation}"
                    )
                    stale = True
        return ArtifactValidation(not errors, stale, tuple(errors))

    def read_page(self, artifact_id, page_index, generation=None, *, check_dependencies=True):
        manifest = self.manifest(artifact_id, generation)
        if manifest is None:
            raise KeyError(artifact_id)
        index = int(page_index)
        if index < 0 or index >= manifest.page_count:
            raise IndexError(index)
        result = self.validate(
            artifact_id, manifest.generation, check_dependencies=check_dependencies
        )
        if not result.valid:
            raise ValueError("artifact is not valid: " + ", ".join(result.errors))
        return memoryview(self._records()[manifest.page_keys[index]])

    def stream(self, artifact_id, *, generation=None, chunk_size=4096):
        if int(chunk_size) <= 0:
            raise ValueError("chunk_size must be positive")
        manifest = self.manifest(artifact_id, generation)
        if manifest is None:
            raise KeyError(artifact_id)
        result = self.validate(artifact_id, manifest.generation)
        if not result.valid:
            raise ValueError("artifact is not valid: " + ", ".join(result.errors))
        for key in manifest.page_keys:
            page = self._records()[key]
            for offset in range(0, len(page), int(chunk_size)):
                yield memoryview(page)[offset:offset + int(chunk_size)]

    def rollback(self, artifact_id, target_generation):
        manifest = self.manifest(artifact_id, target_generation)
        if manifest is None:
            raise KeyError((artifact_id, target_generation))
        result = self.validate(artifact_id, target_generation, check_dependencies=False)
        if not result.valid:
            raise ValueError("rollback source is not valid: " + ", ".join(result.errors))
        pages = [self.read_page(
            artifact_id, index, target_generation, check_dependencies=False
        )
                 for index in range(manifest.page_count)]
        dependencies = []
        for dependency, required_generation in manifest.dependencies:
            current = self.manifest(dependency)
            dependencies.append((
                dependency,
                current.generation if current is not None else required_generation,
            ))
        return self.publish(
            artifact_id,
            manifest.kind,
            pages,
            format_version=manifest.format_version,
            dependencies=tuple(dependencies),
            metadata=manifest.metadata,
        )


class CheckpointJournal:
    def __init__(self, initial=b""):
        self._data = bytearray(initial)

    @property
    def bytes(self):
        return bytes(self._data)

    def append_page(self, generation, key, payload):
        payload = bytes(payload)
        h = HEADER.pack(MAGIC, VERSION, PAGE, 0, generation, key, len(payload))
        self._data.extend(h + payload + struct.pack("<I", zlib.crc32(h + payload) & 0xFFFFFFFF))

    def commit(self, generation):
        h = HEADER.pack(MAGIC, VERSION, COMMIT, 0, generation, 0, 0)
        self._data.extend(h + struct.pack("<I", zlib.crc32(h) & 0xFFFFFFFF))

    def recover(self):
        pos = 0
        valid_end = 0
        pending = {}
        committed = {}
        committed_generation = 0
        quarantined = False
        while pos + HEADER.size + 4 <= len(self._data):
            start = pos
            h = bytes(self._data[pos:pos + HEADER.size])
            magic, version, kind, reserved, generation, key, length = HEADER.unpack(h)
            if magic != MAGIC or version != VERSION or reserved != 0 or length > len(self._data) - pos - HEADER.size - 4:
                quarantined = True
                break
            pos += HEADER.size
            payload = bytes(self._data[pos:pos + length])
            pos += length
            stored = struct.unpack_from("<I", self._data, pos)[0]
            pos += 4
            if stored != zlib.crc32(h + payload) & 0xFFFFFFFF:
                quarantined = True
                break
            if kind == PAGE:
                pending.setdefault(generation, {})[key] = payload
            elif kind == COMMIT and generation in pending and generation >= committed_generation:
                committed = dict(pending[generation])
                committed_generation = generation
                pending = {g: p for g, p in pending.items() if g > generation}
            else:
                quarantined = True
                break
            valid_end = pos
            if start == pos:  # defensive against malformed provider adapters
                quarantined = True
                break
        if valid_end < len(self._data):
            quarantined = True
            del self._data[valid_end:]
        return Recovery(committed_generation, committed, valid_end, quarantined)

    def compare_and_commit(self, expected_generation, generation, pages):
        current = self.recover()
        if current.generation != expected_generation or generation <= expected_generation:
            return False
        for key, payload in sorted(pages.items()):
            self.append_page(generation, key, payload)
        self.commit(generation)
        return True

class DurableCheckpointJournal(CheckpointJournal):
    """File-backed adapter for the shared checkpoint journal protocol.

    Data records may be torn; only a CRC-valid commit marker publishes a
    generation.  ``recover`` therefore selects the highest complete
    generation and truncates an incomplete tail before the next append.
    """

    def __init__(self, path):
        self.path = os.fspath(path)
        try:
            with open(self.path, "rb") as stream:
                initial = stream.read()
        except FileNotFoundError:
            initial = b""
        super().__init__(initial)
        self._durable_offset = len(initial)

    def _append_durable(self, record, flush=False):
        with open(self.path, "ab") as stream:
            stream.write(record)
            stream.flush()
            if flush:
                os.fsync(stream.fileno())
        self._durable_offset += len(record)

    def append_page(self, generation, key, payload):
        payload = bytes(payload)
        header = HEADER.pack(MAGIC, VERSION, PAGE, 0, generation, key, len(payload))
        record = header + payload + struct.pack("<I", zlib.crc32(header + payload) & 0xFFFFFFFF)
        super().append_page(generation, key, payload)
        self._append_durable(record, flush=False)

    def commit(self, generation):
        header = HEADER.pack(MAGIC, VERSION, COMMIT, 0, generation, 0, 0)
        record = header + struct.pack("<I", zlib.crc32(header) & 0xFFFFFFFF)
        super().commit(generation)
        self._append_durable(record, flush=True)

    def recover(self):
        result = super().recover()
        if result.valid_end != self._durable_offset:
            with open(self.path, "r+b") as stream:
                stream.truncate(result.valid_end)
                stream.flush()
                os.fsync(stream.fileno())
            self._durable_offset = result.valid_end
        return result

    def invalidate_derived(self, index_generations):
        """Return derived indexes whose source generation is stale."""
        generation = self.recover().generation
        return tuple(sorted(int(index) for index, source_generation in index_generations.items()
                            if int(source_generation) != generation))

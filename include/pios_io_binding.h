#ifndef PIOS_IO_BINDING_H
#define PIOS_IO_BINDING_H

#include <stddef.h>
#include <stdint.h>

#if defined(__STDC_VERSION__) && (__STDC_VERSION__ >= 201112L)
#define PIOS_STATIC_ASSERT(expr, msg) _Static_assert((expr), msg)
#elif defined(__clang__) || defined(__GNUC__)
#define PIOS_STATIC_ASSERT(expr, msg) _Static_assert((expr), msg)
#else
#define PIOS_STATIC_ASSERT_GLUE_(a, b) a##b
#define PIOS_STATIC_ASSERT_GLUE(a, b) PIOS_STATIC_ASSERT_GLUE_(a, b)
#define PIOS_STATIC_ASSERT(expr, msg) \
    typedef char PIOS_STATIC_ASSERT_GLUE(pios_static_assert_, __LINE__)[(expr) ? 1 : -1]
#endif

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Kernel-bound identity/capability token in a context descriptor.
 *
 * Serves I1/D1 by carrying identity chosen by EL1, not EL0.  The spec leaves the
 * principal representation open; keep this as an opaque 64-bit ABI token until
 * the principal/capability format is finalized.
 *
 * TODO(PIOS ABI): replace or version this if principals need a wider capability.
 */
typedef uint64_t pios_principal_t;

/**
 * Binding lifecycle selected per request/stream.
 *
 * Serves I5/I6 and D5: all bindings share pooldesc, descriptor records, and FIFO
 * messages; only lifecycle legality differs.
 */
enum pios_binding_kind {
    UNARY = 0,
    STREAM = 1,
    DUPLEX = 2,
    DATAGRAM = 3,
    IPC = 4
};

/**
 * Per-port ordering freedom for a typed descriptor phase.
 *
 * Serves I7 and D7: STRICT preserves production order, BOUNDED permits
 * intra-phase optimization only, and ALL permits binding-authorized full reorder.
 */
enum pios_reorder_mode {
    STRICT = 0,
    BOUNDED = 1,
    ALL = 2
};

/**
 * Typed response descriptor phase/kind.
 *
 * Serves I3/I6/I7 and D2/D6/D7: the kind identifies immutable response graph
 * phases and terminal/boundary markers that the kernel uses to enforce legality.
 */
enum pios_desc_kind {
    DESC_PREAMBLE = 0,
    DESC_HEADER = 1,
    DESC_BODY = 2,
    DESC_TRAILER = 3,
    DESC_CONTROL = 4,
    DESC_COMMIT = 5,
    DESC_ABORT = 6,
    DESC_UPGRADE = 7
};

/**
 * DESC_CONTROL subtype values.
 *
 * Serves I6/I7 and D7: explicit in-band control markers avoid kernel heuristics
 * for flushing, checkpoints, stream end, and informational HTTP responses.
 */
enum pios_ctrl_subtype {
    FLUSH = 0,
    CHECKPOINT = 1,
    END_STREAM = 2,
    CONTINUE_100 = 3,
    EARLY_HINTS_103 = 4
};

/**
 * pooldesc.owner values.
 *
 * Serves I2/I4/I8 and D3/D6: ownership is linear and moves between the capsule
 * worker and the kernel; FREE denotes an unused pool slot.
 */
enum pios_pool_owner {
    FREE = 0,
    THREAD = 1,
    KERNEL = 2
};

/**
 * pooldesc.state bit flags.
 *
 * Serves I4/I8 and D3/D8: every lease is used, released, revoked, or eventually
 * ACKed so the kernel can reclaim pool-backed spans.
 */
enum pios_pool_state {
    USED = 1u << 0,
    RELEASED = 1u << 1,
    REVOKED = 1u << 2
};

/**
 * One pool-allocated lease over kernel-authoritative bytes.
 *
 * Serves I2/I4/I5/I8 and D3/D6/D8: EL0 can access only leased spans, ownership is
 * never shared, descriptors cross capsules only via FIFOs, and leases are
 * eventually released, revoked, or ACKed.
 */
struct pooldesc {
    void *ptr;
    uint32_t len;
    uint16_t kind;
    uint8_t owner;
    uint8_t state;
};

/**
 * pios_ctx_desc.body_mode values.
 *
 * Serves I1/I4/I8 and D1/D3/D4/D8: request bodies are either length-bounded
 * materialized leases or cursor-pulled leases, never raw transport streams.
 */
enum pios_body_mode {
    PIOS_BODY_INLINE = 0,
    PIOS_BODY_STREAM = 1
};

/**
 * Kernel-bound invocation context delivered by CTX_READY.
 *
 * Serves I1/I4/I8 and D1/D3/D4/D8: the kernel fixes message boundaries, binds
 * the principal, leases parsed headers/body spans, and ties all FIFO traffic to
 * a connection-scoped sequence or stream id.
 */
struct pios_ctx_desc {
    uint32_t seq;
    uint16_t binding_kind;
    uint16_t header_count;
    pios_principal_t principal;
    struct pooldesc *headers;
    uint8_t body_mode;
    uint8_t reserved0[7];
    union {
        struct {
            struct pooldesc *spans;
            uint16_t span_count;
            uint16_t reserved;
        } inline_body;
        struct {
            uint32_t cursor;
            uint32_t hint_len;
        } stream_body;
    } body;
};

/**
 * pios_desc.flags values.
 *
 * Serves I6/I7 and D7 plus HTTP edge cases from §8: flags make HEAD suppression,
 * known Content-Length, and range mode explicit rather than heuristic.
 *
 * TODO(PIOS ABI): model compression/transform attributes before CL finalization.
 */
enum pios_desc_flags {
    PIOS_DESC_FLAG_HEAD_SUPPRESS = 1u << 0,
    PIOS_DESC_FLAG_CONTENT_LENGTH_KNOWN = 1u << 1,
    PIOS_DESC_FLAG_RANGE_MODE = 1u << 2
};

/**
 * Typed response descriptor record.
 *
 * Serves I2/I3/I6/I7/I8 and D2/D6/D7/D8: EL0 builds typed, phased descriptors in
 * an iso arena; seal moves ownership to EL1 and RESP_SENT ACKs release them.
 */
struct pios_desc {
    uint16_t kind;
    uint16_t subtype;
    struct pooldesc span;
    uint32_t flags;
};

/**
 * pios_port_cfg.flags values.
 *
 * Serves I6/I7 and D5/D7: per-listener policy is separate from per-request
 * binding kind.  Only range mode is named by the current spec.
 *
 * TODO(PIOS ABI): define TLS identity, principal-binding policy, timeouts, quotas,
 * and transform/compression policy fields when those designs are finalized.
 */
enum pios_port_flags {
    PIOS_PORT_FLAG_RANGE_MODE = 1u << 0
};

/**
 * Per-listener binding policy.
 *
 * Serves I6/I7 and D4/D5/D7: the port fixes reorder policy, inline-body cutoff,
 * and default lifecycle before individual requests choose their binding kind.
 */
struct pios_port_cfg {
    uint16_t reorder_mode;
    uint16_t reserved0;
    uint32_t body_inline_max;
    uint16_t default_kind;
    uint16_t reserved1;
    uint32_t flags;
    uint32_t reserved[4];
};

/**
 * FIFO message type tag.
 *
 * Serves I5/I8 and D3/D5/D8: all descriptor movement and completion/revocation
 * crosses EL0/EL1 through kernel-mediated FIFO messages with a fixed record tag.
 */
enum pios_fifo_msg {
    CTX_READY = 0,
    BODY_PULL = 1,
    BODY_CHUNK = 2,
    RESP_SEAL = 3,
    RESP_WRITE = 4,
    RESP_END = 5,
    RESP_FAULT = 6,
    RESP_SENT = 7,
    LEASE_REVOKE = 8
};

/**
 * CTX_READY payload.
 *
 * Serves I1/I4/I8 and D1/D3/D4/D8 by starting a worker invocation with a
 * kernel-authoritative context descriptor.
 */
struct pios_fifo_ctx_ready {
    struct pios_ctx_desc ctx;
};

/**
 * BODY_PULL payload.
 *
 * Serves I1/I4 and D1/D4: EL0 asks EL1 for at most max bytes from a bounded body
 * cursor instead of reading past kernel framing.
 */
struct pios_fifo_body_pull {
    uint32_t seq;
    uint32_t cursor;
    uint32_t max;
};

/**
 * BODY_CHUNK payload.
 *
 * Serves I4/I8 and D3/D4/D8: EL1 returns a leased body span plus an EOF marker,
 * and the lease remains revocable/releasable.
 */
struct pios_fifo_body_chunk {
    uint32_t seq;
    struct pooldesc span;
    uint8_t eof;
    uint8_t reserved[3];
};

/**
 * RESP_SEAL payload.
 *
 * Serves I2/I3/I6 and D2/D6: commits immutable preamble/header descriptors.  A
 * zero status means no final status was supplied in this message.
 */
struct pios_fifo_resp_seal {
    uint32_t seq;
    uint16_t status;
    uint16_t desc_count;
    struct pios_desc *descs;
};

/**
 * RESP_WRITE payload.
 *
 * Serves I3/I6/I8 and D2/D6/D8: appends body descriptors after seal in stream
 * mode, preserving sealed preamble/header immutability.
 */
struct pios_fifo_resp_write {
    uint32_t seq;
    uint16_t desc_count;
    uint16_t reserved;
    struct pios_desc *descs;
};

/**
 * RESP_END payload.
 *
 * Serves I3/I6/I8 and D2/D6/D8: completes a response, optionally carrying final
 * status and trailer descriptors before kernel ownership persists to TX ACK.
 */
struct pios_fifo_resp_end {
    uint32_t seq;
    uint16_t status;
    uint16_t desc_count;
    struct pios_desc *descs;
};

/**
 * RESP_FAULT payload.
 *
 * Serves I6 and D2/D6: abandons the response graph; the kernel chooses clean 500
 * before flush or teardown after committed bytes.
 */
struct pios_fifo_resp_fault {
    uint32_t seq;
};

/**
 * RESP_SENT payload.
 *
 * Serves I8 and D8: ACKs TX completion so outbound descriptors can be released
 * back to the capsule pool.
 */
struct pios_fifo_resp_sent {
    uint32_t seq;
};

/**
 * LEASE_REVOKE payload.
 *
 * Serves I4/I8 and D3/D8: EL1 reclaims a lease under pressure; later validated
 * EL0 access must fault rather than observe stale memory.
 */
struct pios_fifo_lease_revoke {
    struct pooldesc span;
};

/**
 * Fixed-size tagged FIFO message record.
 *
 * Serves I5/I8 and D5/D8: descriptors never cross capsule boundaries except via
 * typed FIFO records, and every request/stream operation is sequence-correlated.
 */
struct pios_fifo_message {
    uint16_t type;
    uint16_t reserved;
    union {
        struct pios_fifo_ctx_ready ctx_ready;
        struct pios_fifo_body_pull body_pull;
        struct pios_fifo_body_chunk body_chunk;
        struct pios_fifo_resp_seal resp_seal;
        struct pios_fifo_resp_write resp_write;
        struct pios_fifo_resp_end resp_end;
        struct pios_fifo_resp_fault resp_fault;
        struct pios_fifo_resp_sent resp_sent;
        struct pios_fifo_lease_revoke lease_revoke;
    } u;
};

PIOS_STATIC_ASSERT(sizeof(void *) == 8u, "PIOS I/O ABI currently requires 64-bit pointers");
PIOS_STATIC_ASSERT(sizeof(struct pooldesc) == 16u, "pooldesc must remain a 16-byte lease record");
PIOS_STATIC_ASSERT(offsetof(struct pooldesc, ptr) == 0u, "pooldesc.ptr ABI offset");
PIOS_STATIC_ASSERT(offsetof(struct pooldesc, len) == 8u, "pooldesc.len ABI offset");
PIOS_STATIC_ASSERT(offsetof(struct pooldesc, kind) == 12u, "pooldesc.kind ABI offset");
PIOS_STATIC_ASSERT(offsetof(struct pooldesc, owner) == 14u, "pooldesc.owner ABI offset");
PIOS_STATIC_ASSERT(offsetof(struct pooldesc, state) == 15u, "pooldesc.state ABI offset");
PIOS_STATIC_ASSERT(sizeof(struct pios_ctx_desc) == 48u, "pios_ctx_desc ABI size");
PIOS_STATIC_ASSERT(offsetof(struct pios_ctx_desc, body) == 32u, "pios_ctx_desc.body ABI offset");
PIOS_STATIC_ASSERT(sizeof(struct pios_desc) == 32u, "pios_desc ABI size");
PIOS_STATIC_ASSERT(offsetof(struct pios_desc, span) == 8u, "pios_desc.span ABI offset");
PIOS_STATIC_ASSERT(sizeof(struct pios_port_cfg) == 32u, "pios_port_cfg ABI size");
PIOS_STATIC_ASSERT(sizeof(struct pios_fifo_message) == 56u, "pios_fifo_message ABI size");

#ifdef __cplusplus
}
#endif

#endif /* PIOS_IO_BINDING_H */

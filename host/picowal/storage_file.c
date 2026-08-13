#ifndef _WIN32
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#endif

/* storage_file.c -- portable, file-backed pv_storage_hook for PicoScript.
 *
 * Replaces PicoWAL's raw SD-block device with a plain OS file. Same pack/card
 * CRUD semantics (numeric pack id + auto-increment card id -> byte blob),
 * but the backing medium is a single data file that works unmodified on
 * Windows, Linux, and macOS (fopen/fread/fwrite/fseek only -- no block IOCTLs).
 *
 * On-disk format ("picowal host file", all integers little-endian):
 *   [8-byte magic "PWALHOST"][4-byte version=2]
 *   repeated records:
 *     [4-byte pack][4-byte id][4-byte len][4-byte CRC32][len bytes payload]
 *     tombstone: len == 0xFFFFFFFF marks a deleted record (id still consumed)
 * Version 1 records (without CRC) remain readable and append in their legacy
 * layout. New files always use version 2. Recovery trusts only the valid
 * prefix and truncates a torn, truncated, oversized, or bad-CRC tail.
 *
 * A record is appended for every Add/Update; reads scan a small in-memory
 * index (pack,id) -> file offset built at startup, so lookups are O(1) after
 * boot and writes are O(1) amortized (simple append log + compaction TODO).
 */
#include "picovm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* Thread safety: g_file/g_index/g_next_id/g_cur_pack/g_query_results are all
 * process-wide globals (single append-log file + in-memory index), by design
 * -- this is a single-writer store, not a per-connection one. picovm_pool.c
 * can run several worker threads concurrently, so two simultaneous requests
 * (e.g. two /query calls, or a /query racing an Add/Update) could otherwise
 * interleave fseek+fread/fwrite on the same FILE*, corrupt g_index, or clobber
 * g_query_results/g_cur_pack mid-query. Rather than partially fixing this by
 * moving only g_cur_pack/g_query_results into pv_ctx (which would still leave
 * the shared file/index racy), pv_storage_file_hook() takes a single
 * process-wide mutex around the whole dispatch, serializing all Storage.*
 * calls. This matches the store's actual single-writer architecture and is
 * portable (CRITICAL_SECTION on Windows, pthread_mutex_t elsewhere). */
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <io.h>
static CRITICAL_SECTION g_pwf_lock;
static int g_pwf_lock_ready;
static void pwf_lock_init(void) { if (!g_pwf_lock_ready) { InitializeCriticalSection(&g_pwf_lock); g_pwf_lock_ready = 1; } }
static void pwf_lock(void) { EnterCriticalSection(&g_pwf_lock); }
static void pwf_unlock(void) { LeaveCriticalSection(&g_pwf_lock); }
#else
#include <pthread.h>
#include <sys/types.h>
#include <unistd.h>
static pthread_mutex_t g_pwf_lock = PTHREAD_MUTEX_INITIALIZER;
static void pwf_lock_init(void) { /* statically initialized */ }
static void pwf_lock(void) { pthread_mutex_lock(&g_pwf_lock); }
static void pwf_unlock(void) { pthread_mutex_unlock(&g_pwf_lock); }
#endif

/* Keep in sync with vm/pico_hooks.h */
#define HOOK_GETSCHEMAFORPACK 0x60
#define HOOK_SETSCHEMAFORPACK 0x61
#define HOOK_ADDCARD    0x62
#define HOOK_UPDATECARD 0x63
#define HOOK_DELETECARD 0x64
#define HOOK_PATCHCARD  0x65
#define HOOK_READCARD   0x66
#define HOOK_QUERYCARD  0x67
#define HOOK_USEPACK    0x68
#define HOOK_EDITCARD   0x69
#define HOOK_GETFIELD   0x6A
#define HOOK_SETFIELD   0x6B
#define HOOK_SETFIELDSTR 0x6C
#define HOOK_GETFIELDSTR 0x6D
#define HOOK_QUERYRESULT 0x6E
#define HOOK_READY      0x6F
#define HOOK_SETSLICE   0x1A0
#define HOOK_CARDLEN    0x1A1
#define HOOK_READSLICE  0x1A2
#define HOOK_WRITESLICE 0x1A3
#define HOOK_ISUSERPACK 0x1A4
#define HOOK_PUTCARD     0x1A5
#define HOOK_READEXACT   0x1A6
#define HOOK_DELETEEXACT 0x1A7
#define HOOK_EXISTS      0x1A8
#define HOOK_SCANNEXT    0x1A9
#define HOOK_SYNC        0x1AA
#define HOOK_RECOVER     0x1AB
#define HOOK_JSON_PARSE 0x340
#define HOOK_MAP_HASS   0x32F
#define HOOK_MAP_FREE   0x321
#define HOOK_MAP_GETSI  0x32E
#define HOOK_MAP_GETSS  0x332

/* Schema store: schemas are themselves records in the same append-log engine,
 * parked at a reserved pack number (2), matching picoweb's picowal
 * convention ("pack 2 = schema store"). Storage.SetSchemaForPack(pack, span)
 * writes {"fields":[{"name":...,"type":...},...]} (the WebIDE Schema
 * Designer's native wire format, JSON.stringify'd) at (PWF_SCHEMA_PACK,pack);
 * Storage.GetSchemaForPack(pack) reads it back; Add/UpdateCard validate every
 * write against it (permissive when no schema is bound for that pack). */
#define PWF_SCHEMA_PACK 2

#define PWF_MAGIC "PWALHOST"
#define PWF_VERSION_LEGACY 1
#define PWF_VERSION 2
#define PWF_MAX_INDEX 65536
#define PWF_MAX_VALUE 4096
#define PWF_FILE_HEADER_BYTES 12

/* UpdateCard needs three logical operands (pack, id, payload span) but the
 * host-hook ABI is a strict 2-in/1-out call. Same idiom the PicoScript
 * reference VM (picoscript_vm.py PicoStoreHost._storage) already uses for
 * its dict-backed store: Storage.UsePack(pack) selects the pack first (a
 * 1-real-arg call), then Storage.UpdateCard(id, bodySpan) is a genuine 2-arg
 * call against that selected pack. AddCard/ReadCard/DeleteCard keep their
 * existing explicit-pack signature (unchanged, so router.eng and any other
 * caller written against the original 2-arg AddCard(pack,body)/
 * ReadCard(pack,id)/DeleteCard(pack,id) still works). */
static int32_t g_cur_pack = 0;
static int32_t g_edit_id = 0;       /* Storage.EditCard active id */
static int32_t g_slice_off = 0;     /* Storage.SetSlice window */
static int32_t g_slice_len = 0;

/* QueryCard/QueryResult (also the UsePack idiom: querying operates on
 * g_cur_pack). Results are card ids matching the query, enumerated via
 * QueryResult(index) exactly like picoscript_vm.py's PicoStoreHost. */
#define PWF_MAX_QUERY_RESULTS 4096
static int32_t g_query_results[PWF_MAX_QUERY_RESULTS];
static int32_t g_query_count = 0;

typedef struct {
    int32_t pack;
    int32_t id;
    int64_t offset;   /* offset of the length-prefixed payload in the file */
    int32_t len;       /* -1 = deleted */
} pwf_index_entry;

static FILE *g_file = NULL;
static uint32_t g_version = PWF_VERSION;
static uint64_t g_recovery_end = PWF_FILE_HEADER_BYTES;
static pwf_index_entry g_index[PWF_MAX_INDEX];
static int g_index_count = 0;
static int g_index_incomplete = 0;
static int32_t g_next_id[4096]; /* per-pack auto-increment counter */

static pwf_index_entry *pwf_find(int32_t pack, int32_t id) {
    for (int i = g_index_count - 1; i >= 0; i--) {
        if (g_index[i].pack == pack && g_index[i].id == id) return &g_index[i];
    }
    return NULL;
}

static void pwf_index_add(int32_t pack, int32_t id, int64_t offset, int32_t len) {
    if (g_index_count >= PWF_MAX_INDEX) { g_index_incomplete = 1; return; }
    g_index[g_index_count].pack = pack;
    g_index[g_index_count].id = id;
    g_index[g_index_count].offset = offset;
    g_index[g_index_count].len = len;
    g_index_count++;
}

static uint32_t pwf_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void pwf_put_le32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16); p[3] = (uint8_t)(value >> 24);
}

static uint32_t pwf_crc_update(uint32_t crc, const uint8_t *data, size_t len) {
    while (len--) {
        crc ^= *data++;
        for (int bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return crc;
}

static uint32_t pwf_record_crc(const uint8_t header[12], const uint8_t *data, int32_t len) {
    uint32_t crc = pwf_crc_update(0xFFFFFFFFu, header, 12);
    if (len > 0) crc = pwf_crc_update(crc, data, (size_t)len);
    return ~crc;
}

static int pwf_truncate(uint64_t size) {
    if (!g_file || fflush(g_file) != 0) return 0;
#ifdef _WIN32
    return _chsize_s(_fileno(g_file), size) == 0;
#else
    return ftruncate(fileno(g_file), (off_t)size) == 0;
#endif
}

static int pwf_storage_recover_impl(void) {
    uint8_t header[16];
    uint8_t payload[PWF_MAX_VALUE];
    uint64_t valid_end = PWF_FILE_HEADER_BYTES;

    g_index_count = 0;
    g_index_incomplete = 0;
    g_query_count = 0;
    g_cur_pack = 0;
    g_edit_id = 0;
    g_slice_off = 0;
    g_slice_len = 0;
    memset(g_next_id, 0, sizeof(g_next_id));
    if (!g_file || fseek(g_file, PWF_FILE_HEADER_BYTES, SEEK_SET) != 0) return -1;

    for (;;) {
        int64_t rec_off = (int64_t)ftell(g_file);
        size_t got = fread(header, 1, 12, g_file);
        if (got == 0 && feof(g_file)) break;
        if (got != 12) break;

        int32_t pack = (int32_t)pwf_le32(header);
        int32_t id = (int32_t)pwf_le32(header + 4);
        int32_t len = (int32_t)pwf_le32(header + 8);
        uint32_t stored_crc = 0;
        int64_t payload_off = rec_off + 12;

        if (pack < 0 || pack >= 4096 || id < 0 ||
            (len < 0 && len != -1) || len > PWF_MAX_VALUE) break;
        if (g_version == PWF_VERSION) {
            if (fread(header + 12, 1, 4, g_file) != 4) break;
            stored_crc = pwf_le32(header + 12);
            payload_off += 4;
        }
        if (len > 0 && fread(payload, 1, (size_t)len, g_file) != (size_t)len) break;
        if (g_version == PWF_VERSION && stored_crc != pwf_record_crc(header, payload, len)) break;

        pwf_index_add(pack, id, payload_off, len);
        if (id >= g_next_id[pack]) g_next_id[pack] = id + 1;
        valid_end = (uint64_t)ftell(g_file);
    }

    clearerr(g_file);
    if (fseek(g_file, 0, SEEK_END) != 0) return -1;
    uint64_t physical_end = (uint64_t)ftell(g_file);
    if (physical_end != valid_end && !pwf_truncate(valid_end)) return -1;
    g_recovery_end = valid_end;
    return g_index_incomplete ? -4 : 0;
}

/* Open (creating if necessary) the backing file and rebuild the in-memory
 * index by scanning it once. Call this before pv_pool_run(). */
int pwf_storage_open(const char *path) {
    uint8_t super[PWF_FILE_HEADER_BYTES];

    pwf_lock_init();
    g_index_count = 0;
    g_index_incomplete = 0;
    g_query_count = 0;
    g_cur_pack = 0;
    g_edit_id = 0;
    g_slice_off = 0;
    g_slice_len = 0;
    memset(g_next_id, 0, sizeof(g_next_id));

    g_file = fopen(path, "r+b");
    if (!g_file) {
        g_file = fopen(path, "w+b");
        if (!g_file) return -1;
        memcpy(super, PWF_MAGIC, 8);
        pwf_put_le32(super + 8, PWF_VERSION);
        if (fwrite(super, 1, sizeof(super), g_file) != sizeof(super) || fflush(g_file) != 0) {
            fclose(g_file); g_file = NULL; return -1;
        }
        g_version = PWF_VERSION;
        g_recovery_end = PWF_FILE_HEADER_BYTES;
        return 0;
    }

    size_t super_len = fread(super, 1, sizeof(super), g_file);
    if (super_len == 0 && feof(g_file)) {
        clearerr(g_file);
        memcpy(super, PWF_MAGIC, 8);
        pwf_put_le32(super + 8, PWF_VERSION);
        if (fseek(g_file, 0, SEEK_SET) != 0 ||
            fwrite(super, 1, sizeof(super), g_file) != sizeof(super) || fflush(g_file) != 0) {
            fclose(g_file); g_file = NULL; return -1;
        }
        g_version = PWF_VERSION;
        g_recovery_end = PWF_FILE_HEADER_BYTES;
        return 0;
    }
    if (super_len != sizeof(super) || memcmp(super, PWF_MAGIC, 8) != 0) {
        fclose(g_file);
        g_file = NULL;
        return -2;
    }
    g_version = pwf_le32(super + 8);
    if (g_version != PWF_VERSION_LEGACY && g_version != PWF_VERSION) {
        fclose(g_file); g_file = NULL; return -3;
    }
    int recovered = pwf_storage_recover_impl();
    if (recovered != 0) { fclose(g_file); g_file = NULL; }
    return recovered;
}

void pwf_storage_close(void) {
    if (g_file) { fflush(g_file); fclose(g_file); g_file = NULL; }
}

int pwf_storage_sync(void) {
    int result;
    pwf_lock(); result = (g_file && fflush(g_file) == 0) ? 0 : -1; pwf_unlock();
    return result;
}

int pwf_storage_recover(void) {
    int result;
    pwf_lock(); result = pwf_storage_recover_impl(); pwf_unlock();
    return result;
}

uint64_t pwf_storage_recovery_offset(void) { return g_recovery_end; }

static int pwf_append(int32_t pack, int32_t id, const uint8_t *data, int32_t len) {
    uint8_t header[16];
    if (!g_file || pack < 0 || pack >= 4096 || id < 0 ||
        (len < 0 && len != -1) || len > PWF_MAX_VALUE || (len > 0 && !data)) return -1;
    if (g_index_count >= PWF_MAX_INDEX) { g_index_incomplete = 1; return -2; }
    if (fseek(g_file, 0, SEEK_END) != 0) return -1;
    int64_t rec_off = (int64_t)ftell(g_file);
    pwf_put_le32(header, (uint32_t)pack);
    pwf_put_le32(header + 4, (uint32_t)id);
    pwf_put_le32(header + 8, (uint32_t)len);
    size_t header_len = 12;
    if (g_version == PWF_VERSION) {
        pwf_put_le32(header + 12, pwf_record_crc(header, data, len));
        header_len = 16;
    }
    if (fwrite(header, 1, header_len, g_file) != header_len ||
        (len > 0 && fwrite(data, 1, (size_t)len, g_file) != (size_t)len) || fflush(g_file) != 0) {
        clearerr(g_file); pwf_truncate((uint64_t)rec_off); return -1;
    }
    pwf_index_add(pack, id, rec_off + (int64_t)header_len, len);
    g_recovery_end = (uint64_t)rec_off + header_len + (len > 0 ? (uint32_t)len : 0u);
    return 0;
}

/* -- local span/arena helpers (mirrors the static helpers in picovm.c; the
 *    fields they touch are all public in pv_ctx, so we re-implement rather
 *    than depend on picovm.c internals). -- */
static uint32_t h_span_ptr(pv_ctx *ctx, int h) {
    return (h > 0 && h < ctx->span_count) ? ctx->span_ptr[h] : 0;
}
static int32_t h_span_len(pv_ctx *ctx, int h) {
    return (h > 0 && h < ctx->span_count) ? ctx->span_len[h] : 0;
}
static int h_span_from_bytes(pv_ctx *ctx, const uint8_t *data, int32_t len) {
    uint32_t k = 0;
    if (!ctx->mem || len < 0) return 0;
    if ((uint64_t)ctx->arena_top + (uint32_t)len > (uint64_t)ctx->mem_size) return 0;
    for (int32_t i = 0; i < len; i++) ctx->mem[ctx->arena_top + k++] = data[i];
    if (ctx->span_count >= PV_MAX_SPANS) return 0;
    int h = ctx->span_count++;
    ctx->span_ptr[h] = ctx->arena_top;
    ctx->span_len[h] = (int32_t)k;
    ctx->arena_top += k;
    return h;
}

/* Scan schema JSON `{"fields":[{"name":"x","type":"y"},...], ...}` for
 * successive (name,type) pairs. Deliberately not a general JSON parser --
 * this schema shape is always produced by the WebIDE Schema Designer's
 * JSON.stringify({fields:[{name,type}]}), so a forgiving substring scan is
 * sufficient and far smaller than a recursive-descent parser. Advances *pos
 * past each field found; returns 0 once no more "name" keys are found. */
static int pwf_next_field(const uint8_t *b, int32_t len, int32_t *pos,
                           char *name, int namecap, char *type, int typecap) {
    int32_t i = *pos, ns, nn, ts, tn;
    for (; i + 6 < len; i++) if (memcmp(b + i, "\"name\"", 6) == 0) break;
    if (i + 6 >= len) return 0;
    i += 6;
    while (i < len && b[i] != '"') i++;
    if (i >= len) return 0;
    i++; ns = i;
    while (i < len && b[i] != '"') i++;
    nn = i - ns; if (nn >= namecap) nn = namecap - 1; if (nn < 0) nn = 0;
    memcpy(name, b + ns, (size_t)nn); name[nn] = 0;
    i++;
    for (; i + 6 < len; i++) if (memcmp(b + i, "\"type\"", 6) == 0) break;
    if (i + 6 >= len) return 0;
    i += 6;
    while (i < len && b[i] != '"') i++;
    if (i >= len) return 0;
    i++; ts = i;
    while (i < len && b[i] != '"') i++;
    tn = i - ts; if (tn >= typecap) tn = typecap - 1; if (tn < 0) tn = 0;
    memcpy(type, b + ts, (size_t)tn); type[tn] = 0;
    i++;
    *pos = i;
    return 1;
}

/* Same string-vs-int classification as the WebIDE's sdStrType() helper
 * (gen_site.py) and BareMetal.WorkflowPico.js's isStrFieldType(). */
static int pwf_type_is_str(const char *t) {
    return strcmp(t, "str") == 0 || strcmp(t, "utf8") == 0 ||
           strcmp(t, "latin1") == 0 || strcmp(t, "blob") == 0;
}

/* Validate a card payload (raw JSON bytes) against the schema bound to
 * `pack` (see PWF_SCHEMA_PACK above). No schema bound -> permissive (returns
 * 1, matching Cards/Query's existing "schema-bound is opt-in" behaviour).
 * Every declared field must be present with a type-consistent JSON value:
 * int-like schema types need an int/bool JSON value, str-like types need a
 * JSON string. Reuses the VM's own JSON.Parse + Map machinery (pv_host2)
 * rather than a second parser for the payload itself -- only the schema's
 * own (simple, WebIDE-authored) shape gets the lightweight scan above. */
static int pwf_validate(pv_ctx *ctx, int32_t pack, const uint8_t *buf, int32_t n) {
    pwf_index_entry *se = pwf_find(PWF_SCHEMA_PACK, pack);
    uint8_t sbuf[4096];
    int32_t slen, pos;
    int h;
    int64_t mi;
    char name[64], type[16];
    int result = 1;

    if (!se || se->len <= 0) return 1; /* not schema-bound: permissive */
    slen = se->len; if (slen > (int32_t)sizeof(sbuf)) slen = sizeof(sbuf);
    fseek(g_file, (long)se->offset, SEEK_SET);
    if (fread(sbuf, 1, (size_t)slen, g_file) != (size_t)slen) return 1; /* unreadable schema: fail open */

    h = h_span_from_bytes(ctx, buf, n);
    if (!h && n > 0) return 0; /* payload too large for arena -- fail closed */
    mi = pv_host2(ctx, HOOK_JSON_PARSE, h, 0);
    if (mi == 0) return 0; /* schema is bound but payload isn't parseable JSON */

    pos = 0;
    while (result && pwf_next_field(sbuf, slen, &pos, name, (int)sizeof(name), type, (int)sizeof(type))) {
        int keyh = h_span_from_bytes(ctx, (const uint8_t *)name, (int32_t)strlen(name));
        int64_t present = pv_host2(ctx, HOOK_MAP_HASS, keyh, 0);
        if (!present) { result = 0; break; } /* required field missing */
        {
            int kind = pv_map_value_kind(ctx, keyh); /* 0=int/bool, 1=string, 2=null */
            int wants_str = pwf_type_is_str(type);
            if (wants_str && kind != 1) { result = 0; break; }
            if (!wants_str && kind != 0) { result = 0; break; }
        }
    }
    /* Free the map every time -- see the matching note in pwf_query. Lower
     * risk here (one map per Add/UpdateCard call vs. N per query scan) but
     * still worth not leaking across the whole request/ctx lifetime. */
    pv_host2(ctx, HOOK_MAP_FREE, (int)mi, 0);
    return result;
}

/* Small query language for v1.5 (still a documented, deliberate scope cut
 * from picoweb's full S:/F:/W: multi-PACK-join DSL -- true cross-pack joins
 * are tracked separately, see host-picowal-joins-v2 -- this only extends
 * single-pack filtering): "" (empty) matches every live record in the pack
 * (this is also how "list" works -- List is just Query("")); one or more
 * "field=value" clauses joined by "&" are ANDed together (e.g.
 * "qty=7&note=widget"), each compared as an int if the value parses as one,
 * else as a string. Populates g_query_results[] with matching card ids and
 * returns the match count; QueryResult(index) enumerates them, mirroring
 * picoscript_vm.py's PicoStoreHost.QueryCard/QueryResult.
 *
 * NOTE: like g_cur_pack (UsePack), g_query_results/g_query_count are process
 * globals, not per-connection state -- fine for this single-writer prototype
 * engine but not yet safe for picovm_pool.c's multi-threaded worker pool if
 * two requests query concurrently. Flagged as a follow-up, not fixed here
 * (see host-picowal-thread-safety). */
#define PWF_MAX_QUERY_CLAUSES 8
typedef struct { char field[64]; char value[128]; int is_int; int32_t want_int; } pwf_clause;

static int pwf_parse_clauses(const uint8_t *q, int32_t qlen, pwf_clause *clauses, int max_clauses) {
    int32_t pos = 0, n = 0;
    while (pos < qlen && n < max_clauses) {
        int32_t clause_start = pos, clause_len;
        const uint8_t *amp = memchr(q + pos, '&', (size_t)(qlen - pos));
        clause_len = amp ? (int32_t)(amp - (q + pos)) : (qlen - pos);
        {
            const uint8_t *cq = q + clause_start;
            const uint8_t *eq = memchr(cq, '=', (size_t)clause_len);
            if (eq) {
                int32_t flen = (int32_t)(eq - cq);
                int32_t vlen = clause_len - flen - 1;
                if (flen >= (int32_t)sizeof(clauses[n].field)) flen = (int32_t)sizeof(clauses[n].field) - 1;
                if (vlen >= (int32_t)sizeof(clauses[n].value)) vlen = (int32_t)sizeof(clauses[n].value) - 1;
                if (vlen < 0) vlen = 0;
                memcpy(clauses[n].field, cq, (size_t)flen); clauses[n].field[flen] = 0;
                memcpy(clauses[n].value, eq + 1, (size_t)vlen); clauses[n].value[vlen] = 0;
                {
                    char *end; long v = strtol(clauses[n].value, &end, 10);
                    clauses[n].is_int = (end != clauses[n].value && *end == 0);
                    clauses[n].want_int = (int32_t)v;
                }
                n++;
            }
        }
        pos = clause_start + clause_len + 1; /* skip past this clause and the '&' (if any) */
    }
    return n;
}

static int32_t pwf_query(pv_ctx *ctx, int32_t pack, const uint8_t *q, int32_t qlen) {
    pwf_clause clauses[PWF_MAX_QUERY_CLAUSES];
    int nclauses = 0;
    static int32_t seen[PWF_MAX_INDEX];
    int seen_count = 0, i;

    g_query_count = 0;
    if (qlen > 0) nclauses = pwf_parse_clauses(q, qlen, clauses, PWF_MAX_QUERY_CLAUSES);

    /* Walk the index newest-first per (pack,id); skip ids already resolved
     * so a superseded (updated-over) older entry isn't visited twice. */
    for (i = g_index_count - 1; i >= 0 && g_query_count < PWF_MAX_QUERY_RESULTS; i--) {
        int32_t id; int j, already = 0;
        if (g_index[i].pack != pack) continue;
        id = g_index[i].id;
        for (j = 0; j < seen_count; j++) if (seen[j] == id) { already = 1; break; }
        if (already) continue;
        if (seen_count < PWF_MAX_INDEX) seen[seen_count++] = id;
        if (g_index[i].len < 0) continue; /* tombstoned */
        if (!nclauses) { g_query_results[g_query_count++] = id; continue; }
        {
            uint8_t buf[4096];
            int32_t n = g_index[i].len; if (n > (int32_t)sizeof(buf)) n = (int32_t)sizeof(buf);
            int h; int64_t mi; int all_match = 1, c;
            fseek(g_file, (long)g_index[i].offset, SEEK_SET);
            if (fread(buf, 1, (size_t)n, g_file) != (size_t)n) continue;
            h = h_span_from_bytes(ctx, buf, n);
            mi = pv_host2(ctx, HOOK_JSON_PARSE, h, 0);
            if (mi == 0) continue;
            for (c = 0; c < nclauses && all_match; c++) {
                int keyh = h_span_from_bytes(ctx, (const uint8_t *)clauses[c].field, (int32_t)strlen(clauses[c].field));
                if (!pv_host2(ctx, HOOK_MAP_HASS, keyh, 0)) { all_match = 0; break; }
                if (clauses[c].is_int) {
                    int64_t got = pv_host2(ctx, HOOK_MAP_GETSI, keyh, 0);
                    if (got != clauses[c].want_int) all_match = 0;
                } else {
                    int64_t vh = pv_host2(ctx, HOOK_MAP_GETSS, keyh, 0);
                    int32_t vlen2 = h_span_len(ctx, (int)vh);
                    int32_t wantlen = (int32_t)strlen(clauses[c].value);
                    if (vlen2 != wantlen || (vlen2 > 0 && memcmp(&ctx->mem[h_span_ptr(ctx, (int)vh)], clauses[c].value, (size_t)vlen2) != 0)) all_match = 0;
                }
            }
            /* Free the map immediately -- PV_MAX_MAPS is small (16) and shared
             * for the whole request/ctx lifetime; a query scanning more than a
             * handful of candidate records would otherwise silently exhaust it
             * (pv_new_active_map returns 0 once exhausted, and this code
             * correctly treats mi==0 as "doesn't match" -- so records processed
             * after exhaustion would silently vanish from results with no
             * error). Found via this file's own test suite hitting exactly
             * that ceiling after enough queries accumulated maps in one ctx. */
            pv_host2(ctx, HOOK_MAP_FREE, (int)mi, 0);
            if (all_match) g_query_results[g_query_count++] = id;
        }
    }
    return g_query_count;
}

static int pv_storage_file_hook_impl(pv_ctx *ctx, int hook, int rd, int rs1, int rs2);

int pv_storage_file_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2) {
    pwf_lock();
    int r = pv_storage_file_hook_impl(ctx, hook, rd, rs1, rs2);
    pwf_unlock();
    return r;
}

static int pv_storage_file_hook_impl(pv_ctx *ctx, int hook, int rd, int rs1, int rs2) {
    switch (hook) {
    case HOOK_PUTCARD: {
        int32_t id = ctx->regs[rs1];
        int h = ctx->regs[rs2];
        int32_t n = h_span_len(ctx, h);
        uint32_t p = h_span_ptr(ctx, h);
        uint8_t buf[PWF_MAX_VALUE];
        if (g_cur_pack < 0 || g_cur_pack >= 4096 || id < 0 || id > 0x003fffff ||
            n < 0 || n > PWF_MAX_VALUE) { ctx->regs[rd] = 1; return 1; }
        for (int32_t i = 0; i < n; i++) buf[i] = ctx->mem[p + (uint32_t)i];
        pwf_index_entry *old = pwf_find(g_cur_pack, id);
        int appended = pwf_append(g_cur_pack, id, buf, n);
        if (appended != 0) { ctx->regs[rd] = appended == -2 ? 6 : 4; return 1; }
        if (old) old->len = -1;
        if (id >= g_next_id[g_cur_pack]) g_next_id[g_cur_pack] = id + 1;
        ctx->regs[rd] = 0;
        return 1;
    }
    case HOOK_READEXACT: {
        int32_t id = ctx->regs[rs1];
        pwf_index_entry *e = pwf_find(g_cur_pack, id);
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        uint8_t buf[PWF_MAX_VALUE];
        if (fseek(g_file, (long)e->offset, SEEK_SET) != 0 ||
            fread(buf, 1, (size_t)e->len, g_file) != (size_t)e->len) {
            ctx->regs[rd] = 0; return 1;
        }
        ctx->regs[rd] = h_span_from_bytes(ctx, buf, e->len);
        return 1;
    }
    case HOOK_DELETEEXACT: {
        int32_t id = ctx->regs[rs1];
        pwf_index_entry *e = pwf_find(g_cur_pack, id);
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        if (pwf_append(g_cur_pack, id, NULL, -1) != 0) { ctx->regs[rd] = 0; return 1; }
        e->len = -1;
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_EXISTS: {
        pwf_index_entry *e = pwf_find(g_cur_pack, ctx->regs[rs1]);
        ctx->regs[rd] = (e && e->len >= 0) ? 1 : 0;
        return 1;
    }
    case HOOK_SCANNEXT: {
        int32_t after = ctx->regs[rs1];
        int32_t best = -1;
        for (int i = 0; i < g_index_count; i++) {
            pwf_index_entry *e = &g_index[i];
            if (e->pack == g_cur_pack && e->id > after && e->len >= 0 &&
                pwf_find(e->pack, e->id) == e && (best < 0 || e->id < best)) best = e->id;
        }
        ctx->regs[rd] = best;
        return 1;
    }
    case HOOK_SYNC:
        ctx->regs[rd] = (g_file && fflush(g_file) == 0) ? 0 : -1;
        return 1;
    case HOOK_RECOVER:
        ctx->regs[rd] = pwf_storage_recover_impl();
        return 1;
    case HOOK_GETSCHEMAFORPACK: {
        int32_t pack = ctx->regs[rs1];
        pwf_index_entry *e = pwf_find(PWF_SCHEMA_PACK, pack);
        if (!e || e->len <= 0) { ctx->regs[rd] = 0; return 1; }
        uint8_t sbuf[PWF_MAX_VALUE];
        int32_t slen = e->len; if (slen > (int32_t)sizeof(sbuf)) slen = sizeof(sbuf);
        fseek(g_file, (long)e->offset, SEEK_SET);
        fread(sbuf, 1, (size_t)slen, g_file);
        ctx->regs[rd] = h_span_from_bytes(ctx, sbuf, slen);
        return 1;
    }
    case HOOK_SETSCHEMAFORPACK: {
        int32_t pack = ctx->regs[rs1];
        int h = ctx->regs[rs2];
        uint32_t p = h_span_ptr(ctx, h);
        int32_t n = h_span_len(ctx, h);
        uint8_t buf[PWF_MAX_VALUE];
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        for (int32_t i = 0; i < n; i++) buf[i] = ctx->mem[p + (uint32_t)i];
        /* Same tombstone-then-append idiom as UpdateCard, keyed (SCHEMA_PACK,pack). */
        pwf_index_entry *old = pwf_find(PWF_SCHEMA_PACK, pack);
        if (pwf_append(PWF_SCHEMA_PACK, pack, buf, n) != 0) { ctx->regs[rd] = 0; return 1; }
        if (old) old->len = -1;
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_ADDCARD: {
        int32_t pack = ctx->regs[rs1];
        int h = ctx->regs[rs2];
        uint32_t p = h_span_ptr(ctx, h);
        int32_t n = h_span_len(ctx, h);
        if (pack < 0 || pack >= 4096) { ctx->regs[rd] = -1; return 1; }
        uint8_t buf[PWF_MAX_VALUE];
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        for (int32_t i = 0; i < n; i++) buf[i] = ctx->mem[p + (uint32_t)i];
        if (!pwf_validate(ctx, pack, buf, n)) { ctx->regs[rd] = -2; return 1; } /* -2 = schema validation failed */
        int32_t id = g_next_id[pack]++;
        if (pwf_append(pack, id, buf, n) != 0) { ctx->regs[rd] = -1; return 1; }
        ctx->regs[rd] = id;
        return 1;
    }
    case HOOK_USEPACK: {
        g_cur_pack = ctx->regs[rs1];
        ctx->regs[rd] = g_cur_pack;
        return 1;
    }
    case HOOK_QUERYCARD: {
        /* rs1=query text span, operating on g_cur_pack (Storage.UsePack idiom). */
        int h = ctx->regs[rs1];
        uint32_t p = h_span_ptr(ctx, h);
        int32_t n = h_span_len(ctx, h);
        uint8_t qbuf[192];
        if (n > (int32_t)sizeof(qbuf)) n = sizeof(qbuf);
        for (int32_t i = 0; i < n; i++) qbuf[i] = ctx->mem[p + (uint32_t)i];
        ctx->regs[rd] = pwf_query(ctx, g_cur_pack, qbuf, n);
        return 1;
    }
    case HOOK_QUERYRESULT: {
        int32_t idx = ctx->regs[rs1];
        ctx->regs[rd] = (idx >= 0 && idx < g_query_count) ? g_query_results[idx] : 0;
        return 1;
    }
    case HOOK_UPDATECARD: {
        /* rs1=id, rs2=bodySpan, operating on the pack selected by the most
         * recent Storage.UsePack(pack) call (see idiom note above). */
        int32_t id = ctx->regs[rs1];
        int h = ctx->regs[rs2];
        pwf_index_entry *e = pwf_find(g_cur_pack, id);
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; } /* no such live record */
        uint32_t p = h_span_ptr(ctx, h);
        int32_t n = h_span_len(ctx, h);
        uint8_t buf[PWF_MAX_VALUE];
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        for (int32_t i = 0; i < n; i++) buf[i] = ctx->mem[p + (uint32_t)i];
        if (!pwf_validate(ctx, g_cur_pack, buf, n)) { ctx->regs[rd] = 0; return 1; }
        /* Append-log update: write a new version at a fresh offset, then
         * flip the old index entry to a tombstone so pwf_find (which scans
         * newest-first) resolves to the new one. Same durability model as
         * Add/Delete -- no in-place overwrite, no torn writes. */
        if (pwf_append(g_cur_pack, id, buf, n) != 0) { ctx->regs[rd] = 0; return 1; }
        e->len = -1;
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_DELETECARD: {
        int32_t pack = ctx->regs[rs1];
        int32_t id = ctx->regs[rs2];
        pwf_index_entry *e = pwf_find(pack, id);
        if (!e) { ctx->regs[rd] = 0; return 1; }
        if (pwf_append(pack, id, NULL, -1) != 0) { ctx->regs[rd] = 0; return 1; }
        e->len = -1;
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_READCARD: {
        int32_t pack = ctx->regs[rs1];
        int32_t id = ctx->regs[rs2];
        pwf_index_entry *e = pwf_find(pack, id);
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        uint8_t buf[PWF_MAX_VALUE];
        int32_t n = e->len;
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        fseek(g_file, (long)e->offset, SEEK_SET);
        fread(buf, 1, (size_t)n, g_file);
        ctx->regs[rd] = h_span_from_bytes(ctx, buf, n);
        return 1;
    }
    case HOOK_EDITCARD:
        /* Select card id in current pack (active-record style). */
        g_edit_id = ctx->regs[rs1];
        ctx->regs[rd] = g_edit_id;
        return 1;
    case HOOK_READY:
        ctx->regs[rd] = g_file ? 1 : 0;
        return 1;
    case HOOK_ISUSERPACK: {
        int32_t pack = ctx->regs[rs1];
        ctx->regs[rd] = (pack >= 2) ? 1 : 0;
        return 1;
    }
    case HOOK_CARDLEN: {
        int32_t id = ctx->regs[rs1];
        pwf_index_entry *e = pwf_find(g_cur_pack, id);
        ctx->regs[rd] = (e && e->len >= 0) ? e->len : 0;
        return 1;
    }
    case HOOK_SETSLICE: {
        g_slice_off = ctx->regs[rs1];
        g_slice_len = ctx->regs[rs2];
        if (g_slice_off < 0) g_slice_off = 0;
        if (g_slice_len < 0) g_slice_len = 0;
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_READSLICE: {
        int32_t id = ctx->regs[rs1];
        pwf_index_entry *e = pwf_find(g_cur_pack, id);
        uint8_t buf[PWF_MAX_VALUE];
        int32_t n, off, len;
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        n = e->len;
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        fseek(g_file, (long)e->offset, SEEK_SET);
        fread(buf, 1, (size_t)n, g_file);
        off = g_slice_off;
        if (off > n) off = n;
        len = g_slice_len;
        if (off + len > n) len = n - off;
        ctx->regs[rd] = h_span_from_bytes(ctx, buf + off, len);
        return 1;
    }
    case HOOK_WRITESLICE: {
        /* rs1=id, rs2=payload span written at g_slice_off into existing card */
        int32_t id = ctx->regs[rs1];
        int h = ctx->regs[rs2];
        pwf_index_entry *e = pwf_find(g_cur_pack, id);
        uint8_t buf[PWF_MAX_VALUE];
        uint32_t p;
        int32_t n, pn, i;
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        n = e->len;
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        fseek(g_file, (long)e->offset, SEEK_SET);
        fread(buf, 1, (size_t)n, g_file);
        p = h_span_ptr(ctx, h);
        pn = h_span_len(ctx, h);
        for (i = 0; i < pn; i++) {
            int32_t at = g_slice_off + i;
            if (at >= 0 && at < (int32_t)sizeof(buf)) {
                buf[at] = ctx->mem[p + (uint32_t)i];
                if (at >= n) n = at + 1;
            }
        }
        if (pwf_append(g_cur_pack, id, buf, n) != 0) { ctx->regs[rd] = 0; return 1; }
        e->len = -1;
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_GETFIELD:
    case HOOK_GETFIELDSTR: {
        /* Active card g_edit_id; rs1 = field name span. JSON map lookup. */
        pwf_index_entry *e = pwf_find(g_cur_pack, g_edit_id);
        uint8_t buf[PWF_MAX_VALUE];
        int32_t n;
        int body_h, mi;
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        n = e->len;
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        fseek(g_file, (long)e->offset, SEEK_SET);
        fread(buf, 1, (size_t)n, g_file);
        body_h = h_span_from_bytes(ctx, buf, n);
        mi = (int)pv_host2(ctx, HOOK_JSON_PARSE, body_h, 0);
        if (!mi) { ctx->regs[rd] = 0; return 1; }
        if (hook == HOOK_GETFIELD)
            ctx->regs[rd] = (int32_t)pv_host2(ctx, HOOK_MAP_GETSI, ctx->regs[rs1], 0);
        else
            ctx->regs[rd] = (int32_t)pv_host2(ctx, HOOK_MAP_GETSS, ctx->regs[rs1], 0);
        pv_host2(ctx, HOOK_MAP_FREE, mi, 0);
        return 1;
    }
    case HOOK_SETFIELD:
    case HOOK_SETFIELDSTR: {
        /* Flat JSON merge: parse card, put field, rebuild minimal object. */
        pwf_index_entry *e = pwf_find(g_cur_pack, g_edit_id);
        uint8_t buf[PWF_MAX_VALUE];
        char key[64], val[256], out[PWF_MAX_VALUE];
        int32_t n, kn, vn, i, oi = 0;
        int body_h, mi, key_h, val_h;
        uint32_t kp, vp;
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        n = e->len;
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        fseek(g_file, (long)e->offset, SEEK_SET);
        fread(buf, 1, (size_t)n, g_file);
        body_h = h_span_from_bytes(ctx, buf, n);
        mi = (int)pv_host2(ctx, HOOK_JSON_PARSE, body_h, 0);
        if (!mi) {
            /* non-JSON: replace whole body with {"key":value} */
            kp = h_span_ptr(ctx, ctx->regs[rs1]);
            kn = h_span_len(ctx, ctx->regs[rs1]);
            if (kn > 63) kn = 63;
            for (i = 0; i < kn; i++) key[i] = (char)ctx->mem[kp + (uint32_t)i];
            key[kn] = 0;
            if (hook == HOOK_SETFIELDSTR) {
                vp = h_span_ptr(ctx, ctx->regs[rs2]);
                vn = h_span_len(ctx, ctx->regs[rs2]);
                if (vn > 200) vn = 200;
                for (i = 0; i < vn; i++) val[i] = (char)ctx->mem[vp + (uint32_t)i];
                val[vn] = 0;
                oi = snprintf(out, sizeof(out), "{\"%s\":\"%s\"}", key, val);
            } else {
                oi = snprintf(out, sizeof(out), "{\"%s\":%d}", key, (int)ctx->regs[rs2]);
            }
        } else {
            key_h = ctx->regs[rs1];
            if (hook == HOOK_SETFIELDSTR) {
                /* Map.PutSS key,val — codes 0x331 */
                pv_host2(ctx, 0x331, key_h, ctx->regs[rs2]);
            } else {
                pv_host2(ctx, 0x32D, key_h, ctx->regs[rs2]); /* PutSI */
            }
            /* Rebuild flat JSON from map entries */
            {
                int count = (int)pv_host2(ctx, 0x323, 0, 0); /* Map.Count */
                int k;
                oi = 0;
                out[oi++] = '{';
                for (k = 0; k < count && oi < (int)sizeof(out) - 8; k++) {
                    int ks = (int)pv_host2(ctx, 0x336, k, 0); /* KeySpanAt */
                    int vs = (int)pv_host2(ctx, 0x338, k, 0); /* ValSpanAt */
                    int is_span = (int)pv_host2(ctx, 0x339, k, 0); /* ValIsSpan */
                    int kn2 = h_span_len(ctx, ks), vn2, j;
                    uint32_t kpp = h_span_ptr(ctx, ks);
                    if (k) out[oi++] = ',';
                    out[oi++] = '"';
                    for (j = 0; j < kn2 && oi < (int)sizeof(out) - 4; j++)
                        out[oi++] = (char)ctx->mem[kpp + (uint32_t)j];
                    out[oi++] = '"';
                    out[oi++] = ':';
                    if (is_span) {
                        uint32_t vpp = h_span_ptr(ctx, vs);
                        vn2 = h_span_len(ctx, vs);
                        out[oi++] = '"';
                        for (j = 0; j < vn2 && oi < (int)sizeof(out) - 4; j++)
                            out[oi++] = (char)ctx->mem[vpp + (uint32_t)j];
                        out[oi++] = '"';
                    } else {
                        char num[32];
                        int nl = snprintf(num, sizeof(num), "%d",
                                          (int)pv_host2(ctx, 0x337, k, 0)); /* ValAt */
                        for (j = 0; j < nl && oi < (int)sizeof(out) - 2; j++)
                            out[oi++] = num[j];
                    }
                }
                out[oi++] = '}';
                out[oi] = 0;
            }
            pv_host2(ctx, HOOK_MAP_FREE, mi, 0);
        }
        if (pwf_append(g_cur_pack, g_edit_id, (uint8_t *)out, oi) != 0) {
            ctx->regs[rd] = 0;
            return 1;
        }
        e->len = -1;
        ctx->regs[rd] = 1;
        return 1;
    }
    default:
        if (hook == HOOK_PATCHCARD) {
            /* same as UpdateCard */
            int32_t id = ctx->regs[rs1];
            int h = ctx->regs[rs2];
            pwf_index_entry *e = pwf_find(g_cur_pack, id);
            uint32_t p;
            int32_t n;
            uint8_t buf[PWF_MAX_VALUE];
            if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
            p = h_span_ptr(ctx, h);
            n = h_span_len(ctx, h);
            if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
            for (int32_t i = 0; i < n; i++) buf[i] = ctx->mem[p + (uint32_t)i];
            if (pwf_append(g_cur_pack, id, buf, n) != 0) { ctx->regs[rd] = 0; return 1; }
            e->len = -1;
            ctx->regs[rd] = 1;
            return 1;
        }
        return 0; /* not ours; let the default host no-op it */
    }
}

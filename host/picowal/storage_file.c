/* storage_file.c -- portable, file-backed pv_storage_hook for PicoScript.
 *
 * Replaces PicoWAL's raw SD-block device with a plain OS file. Same pack/card
 * CRUD semantics (numeric pack id + auto-increment card id -> byte blob),
 * but the backing medium is a single data file that works unmodified on
 * Windows, Linux, and macOS (fopen/fread/fwrite/fseek only -- no block IOCTLs).
 *
 * On-disk format ("picowal host file"):
 *   [8-byte magic "PWALHOST"][4-byte version]
 *   repeated records:
 *     [4-byte pack][4-byte id][4-byte len][len bytes payload]
 *     tombstone: len == 0xFFFFFFFF marks a deleted record (id still consumed)
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

/* Keep in sync with vm/pico_hooks.h */
#define HOOK_ADDCARD    0x62
#define HOOK_UPDATECARD 0x63
#define HOOK_DELETECARD 0x64
#define HOOK_READCARD   0x66
#define HOOK_USEPACK    0x68

#define PWF_MAGIC "PWALHOST"
#define PWF_VERSION 1
#define PWF_MAX_INDEX 65536

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

typedef struct {
    int32_t pack;
    int32_t id;
    int64_t offset;   /* offset of the length-prefixed payload in the file */
    int32_t len;       /* -1 = deleted */
} pwf_index_entry;

static FILE *g_file = NULL;
static pwf_index_entry g_index[PWF_MAX_INDEX];
static int g_index_count = 0;
static int32_t g_next_id[4096]; /* per-pack auto-increment counter */

static pwf_index_entry *pwf_find(int32_t pack, int32_t id) {
    for (int i = g_index_count - 1; i >= 0; i--) {
        if (g_index[i].pack == pack && g_index[i].id == id) return &g_index[i];
    }
    return NULL;
}

static void pwf_index_add(int32_t pack, int32_t id, int64_t offset, int32_t len) {
    if (g_index_count >= PWF_MAX_INDEX) return; /* TODO: compaction when full */
    g_index[g_index_count].pack = pack;
    g_index[g_index_count].id = id;
    g_index[g_index_count].offset = offset;
    g_index[g_index_count].len = len;
    g_index_count++;
}

/* Open (creating if necessary) the backing file and rebuild the in-memory
 * index by scanning it once. Call this before pv_pool_run(). */
int pwf_storage_open(const char *path) {
    char magic[8];
    uint32_t version;

    g_file = fopen(path, "r+b");
    if (!g_file) {
        g_file = fopen(path, "w+b");
        if (!g_file) return -1;
        fwrite(PWF_MAGIC, 1, 8, g_file);
        version = PWF_VERSION;
        fwrite(&version, sizeof(version), 1, g_file);
        fflush(g_file);
        return 0;
    }

    if (fread(magic, 1, 8, g_file) != 8 || memcmp(magic, PWF_MAGIC, 8) != 0) {
        fclose(g_file);
        g_file = NULL;
        return -2;
    }
    fread(&version, sizeof(version), 1, g_file);

    for (;;) {
        int64_t rec_off = (int64_t)ftell(g_file);
        int32_t pack, id, len;
        if (fread(&pack, sizeof(pack), 1, g_file) != 1) break;
        if (fread(&id, sizeof(id), 1, g_file) != 1) break;
        if (fread(&len, sizeof(len), 1, g_file) != 1) break;
        int64_t payload_off = rec_off + 12;
        if (len >= 0) {
            if (fseek(g_file, len, SEEK_CUR) != 0) break;
        }
        pwf_index_add(pack, id, payload_off, len);
        if (pack >= 0 && pack < 4096 && id >= g_next_id[pack]) g_next_id[pack] = id + 1;
    }
    return 0;
}

void pwf_storage_close(void) {
    if (g_file) { fflush(g_file); fclose(g_file); g_file = NULL; }
}

static int pwf_append(int32_t pack, int32_t id, const uint8_t *data, int32_t len) {
    if (!g_file) return -1;
    fseek(g_file, 0, SEEK_END);
    int64_t rec_off = (int64_t)ftell(g_file);
    fwrite(&pack, sizeof(pack), 1, g_file);
    fwrite(&id, sizeof(id), 1, g_file);
    fwrite(&len, sizeof(len), 1, g_file);
    if (len > 0) fwrite(data, 1, (size_t)len, g_file);
    fflush(g_file);
    pwf_index_add(pack, id, rec_off + 12, len);
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
    if (!ctx->mem || len <= 0) return 0;
    if ((uint64_t)ctx->arena_top + (uint32_t)len > (uint64_t)ctx->mem_size) return 0;
    for (int32_t i = 0; i < len; i++) ctx->mem[ctx->arena_top + k++] = data[i];
    if (ctx->span_count >= PV_MAX_SPANS) return 0;
    int h = ctx->span_count++;
    ctx->span_ptr[h] = ctx->arena_top;
    ctx->span_len[h] = (int32_t)k;
    ctx->arena_top += k;
    return h;
}

int pv_storage_file_hook(pv_ctx *ctx, int hook, int rd, int rs1, int rs2) {
    switch (hook) {
    case HOOK_ADDCARD: {
        int32_t pack = ctx->regs[rs1];
        int h = ctx->regs[rs2];
        uint32_t p = h_span_ptr(ctx, h);
        int32_t n = h_span_len(ctx, h);
        if (pack < 0 || pack >= 4096) { ctx->regs[rd] = -1; return 1; }
        int32_t id = g_next_id[pack]++;
        uint8_t buf[4096];
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        for (int32_t i = 0; i < n; i++) buf[i] = ctx->mem[p + (uint32_t)i];
        pwf_append(pack, id, buf, n);
        ctx->regs[rd] = id;
        return 1;
    }
    case HOOK_USEPACK: {
        g_cur_pack = ctx->regs[rs1];
        ctx->regs[rd] = g_cur_pack;
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
        uint8_t buf[4096];
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        for (int32_t i = 0; i < n; i++) buf[i] = ctx->mem[p + (uint32_t)i];
        /* Append-log update: write a new version at a fresh offset, then
         * flip the old index entry to a tombstone so pwf_find (which scans
         * newest-first) resolves to the new one. Same durability model as
         * Add/Delete -- no in-place overwrite, no torn writes. */
        e->len = -1;
        pwf_append(g_cur_pack, id, buf, n);
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_DELETECARD: {
        int32_t pack = ctx->regs[rs1];
        int32_t id = ctx->regs[rs2];
        pwf_index_entry *e = pwf_find(pack, id);
        if (!e) { ctx->regs[rd] = 0; return 1; }
        e->len = -1;
        pwf_append(pack, id, NULL, -1);
        ctx->regs[rd] = 1;
        return 1;
    }
    case HOOK_READCARD: {
        int32_t pack = ctx->regs[rs1];
        int32_t id = ctx->regs[rs2];
        pwf_index_entry *e = pwf_find(pack, id);
        if (!e || e->len < 0) { ctx->regs[rd] = 0; return 1; }
        uint8_t buf[4096];
        int32_t n = e->len;
        if (n > (int32_t)sizeof(buf)) n = sizeof(buf);
        fseek(g_file, (long)e->offset, SEEK_SET);
        fread(buf, 1, (size_t)n, g_file);
        ctx->regs[rd] = h_span_from_bytes(ctx, buf, n);
        return 1;
    }
    default:
        return 0; /* not ours; let the default host no-op it */
    }
}
